/*
SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
SPDX-License-Identifier: MIT
*/

import 'dart:async';
import 'private_session.dart';
import 'private_control_policy.dart';
import 'package:dio/dio.dart';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:cupertino_ui/cupertino_ui.dart';

// Project-specific imports.
import '../app_state.dart';
import '../model/msg_type.dart';
import '../util/rsa_util.dart';
import '../util/value_constant.dart';

class WebSocketUtil {
  WebSocketUtil._internal();

  static final WebSocketUtil shared = WebSocketUtil._internal();

  WebSocket? _socket;
  PrivateControlPolicy? _privatePolicy;
  Timer? _policyTimer;
  final ValueNotifier<String> status = ValueNotifier('私有连接尚未建立');
  StreamSubscription? _subscription;

  // Track the connection state so reconnect logs are not repeated.
  bool _isConnected = false;

  Function()? connectionSuccessful;

  final Map<String, void Function(dynamic)> _observers = {};
  final Map<Object, void Function()> _connectionObservers = {};

  String _urlString = '';
  int _connectionGeneration = 0;

  /* =======================
   * Authorization
   * ======================= */
  String getAuthorization(String mac) {
    final rand = Random();
    final randomPart = List.generate(
      mac.length,
      (_) =>
          ValueConstant.characters[rand.nextInt(
            ValueConstant.characters.length,
          )],
    ).join();

    final timestamp = DateTime.now().millisecondsSinceEpoch ~/ 1000;
    return '$mac|$randomPart|$timestamp';
  }

  /* =======================
   * Connect
   * ======================= */
  Future<void> connect(String urlString) async {
    // Disconnect any existing connection first.
    disconnect();
    final generation = _connectionGeneration;
    _urlString = urlString;
    status.value = "正在连接私有服务";

    if (AppState.shared.deviceMac.isEmpty) {
      debugPrint(' WebSocket ConnectFailed:DeviceMACAddressIs null/empty');
      return;
    }

    // Log the start of the connection attempt.

    try {
      final encryptedToken = PrivateSession.enabled
          ? PrivateSession.authorization(Uri.parse(urlString), 'management')
          : RsaUtil.encrypt(getAuthorization(AppState.shared.deviceMac));
      if (PrivateSession.enabled) {
        final policy = await PrivateControlPolicy.fetch(AppState.shared.deviceMac);
        if (generation != _connectionGeneration) return;
        _privatePolicy = policy;
        PrivateControlPolicy.active = policy;
      }
      final headers = {ValueConstant.authorization: encryptedToken};
      final socket = await WebSocket.connect(urlString, headers: headers);
      if (generation != _connectionGeneration) {
        await socket.close(WebSocketStatus.goingAway);
        return;
      }
      _socket = socket;

      // Log successful connection details (time and URL).
      _isConnected = true;
      status.value = '私有服务已连接；媒体功能受设备权限控制';
      if (PrivateSession.enabled) {
        _policyTimer = Timer.periodic(const Duration(seconds: 45), (_) async {
          try {
            final policy = await PrivateControlPolicy.fetch(AppState.shared.deviceMac);
            if (generation == _connectionGeneration) {
              _privatePolicy = policy;
              PrivateControlPolicy.active = policy;
            }
          } catch (_) {
            if (generation != _connectionGeneration) return;
            _privatePolicy = null;
    PrivateControlPolicy.active = null;
            status.value = '设备权限刷新失败，请重新连接或登录';
          }
        });
      }
      final connectTime = DateTime.now().toString().split('.').first;
      debugPrint(' WebSocket ConnectSuccess [$connectTime]');
      debugPrint('   ConnectState: ${_socket?.readyState} (OPEN)');

      _subscription = _socket!.listen(
        (message) { if (generation == _connectionGeneration) _handleMessage(message); },
        onError: (Object error) { if (generation == _connectionGeneration) _handleError(error); },
        onDone: () { if (generation == _connectionGeneration) _handleDone(); },
        cancelOnError: true,
      );

      if (connectionSuccessful != null) {
        connectionSuccessful!();
      }
      for (final observer in List.of(_connectionObservers.values)) {
        observer();
      }
    } catch (e) {
      if (generation != _connectionGeneration) return;
      _isConnected = false;
      if (PrivateSession.enabled && (e is StateError ||
          (e is DioException && [401,403].contains(e.response?.statusCode)))) {
        status.value = '需要有效的私有登录，或当前账号无设备权限';
        return;
      }
      _scheduleReconnect();
    }
  }

  /* =======================
   * Message Handling
   * ======================= */
  void _handleMessage(dynamic message) {
    final isPing = replyPong(message);
    if (!isPing) {
      _notifyObservers(message);
    }
  }

  void _handleError(Object error) {
    // Log runtime errors separately from connection errors.
    _isConnected = false;
    _scheduleReconnect();
  }

  void _handleDone() {
    _policyTimer?.cancel();
    _privatePolicy = null;
    PrivateControlPolicy.active = null;
    if (PrivateSession.enabled && _socket?.closeCode == 1008) {
      _isConnected = false;
      status.value = '会话已过期或权限已撤销，请重新登录';
      return;
    }
    // Log the close event and its reason.
    _isConnected = false;
    final closeTime = DateTime.now().toString().split('.').first;
    debugPrint(' WebSocket ConnectAlready关闭 [$closeTime]');
    _scheduleReconnect();
  }

  bool replyPong(dynamic message) {
    if (message is Uint8List) {
      final result = AppState.shared.parseMessage(message);
      final msgType = result.$1;

      if (msgType != null) {
        switch (msgType) {
          case MsgType.ping:
            AppState.shared.sendWebSocketMessage(.pong);
            return true;
          default:
            return false;
        }
      }
    }
    return false;
  }

  /* =======================
   * Send
   * ======================= */
  void sendString(String message) {
    if (PrivateSession.enabled) {
      status.value = '此消息类型未开放';
      return;
    }
    if (_socket == null) {
      debugPrint(' 发送StringMessageFailed:WebSocket Disconnected');
      return;
    }

    try {
      _socket!.add(message);
    } catch (e) {
      _isConnected = false;
      _scheduleReconnect();
    }
  }

  void send(Uint8List data) {
    if (PrivateSession.enabled && (data.isEmpty ||
        _privatePolicy?.allows(AppState.shared.deviceMac, data[0]) != true)) {
      if (data.isNotEmpty && data[0] == 0x1b) return; // Private LAN advertisement is disabled.
      status.value = '当前设备未允许此功能，或权限已过期';
      return;
    }
    if (_socket == null) {
      debugPrint(' 发送2BaseMessageFailed:WebSocket Disconnected');
      return;
    }

    //debugPrint('📤 send2Basemessage: length=${data.length} Byte');
    try {
      _socket!.add(data);
    } catch (e) {
      _isConnected = false;
      _scheduleReconnect();
    }
  }

  /* =======================
   * Reconnect
   * ======================= */
  void _scheduleReconnect() async {
    if (_urlString.isEmpty) return;
    status.value = "连接中断，正在重连";

    // Log the reconnect attempt without repeating messages.
    if (!_isConnected) {
    }

    final generation = _connectionGeneration;
    await Future.delayed(const Duration(seconds: 1));
    if (generation != _connectionGeneration || _urlString.isEmpty) return;
    await connect(_urlString);
  }

  /* =======================
   * Disconnect
   * ======================= */
  void disconnect() {
    _connectionGeneration++;
    _policyTimer?.cancel();
    _policyTimer = null;
    _privatePolicy = null;
    PrivateControlPolicy.active = null;
    status.value = '私有连接已断开';
    _urlString = '';
    _subscription?.cancel();
    _subscription = null;
    _socket?.close(WebSocketStatus.goingAway, '主动断开连接');
    _isConnected = false;
    _socket = null;
    debugPrint('🔌 WebSocket AlreadyProactiveDisconnectConnect');
  }

  /* =======================
   * Observer
   * ======================= */
  void addObserver(String key, void Function(dynamic message) observer) {
    _observers[key] = observer;
  }

  void removeObserver(String key) {
    _observers.remove(key);
  }

  /// Pages subscribe individually so a shared reconnect callback cannot overwrite another handler or outlive its page.
  void addConnectionObserver(Object key, void Function() observer) {
    _connectionObservers[key] = observer;
  }

  void removeConnectionObserver(Object key) {
    _connectionObservers.remove(key);
  }

  void removeAllObservers() {
    _observers.clear();
  }

  void _notifyObservers(dynamic message) {
    for (final observer in List.of(_observers.values)) {
      observer(message);
    }
  }

  // Current connection state, exposed for external checks.
  bool get isConnected => _isConnected && _socket?.readyState == WebSocket.open;
}
