import 'package:dio/dio.dart';
import 'private_endpoints.dart';
import 'private_session.dart';

/// A short-lived, device-scoped server capability snapshot; never persists grants.
class PrivateControlPolicy {
  static PrivateControlPolicy? active;
  static bool mediaAllowed(int kind) {
    if (!PrivateSession.enabled) return true;
    try {
      PrivateSession.token('management');
      return active?.allows(active!.mac, kind) ?? false;
    } on StateError {
      return false;
    }
  }
  PrivateControlPolicy._(this.mac, this.commands, this.expires);
  final String mac;
  final Set<int> commands;
  final DateTime expires;

  factory PrivateControlPolicy.parse(String mac, dynamic data, {DateTime? now}) {
    if (!RegExp(r'^[0-9a-f]{12}$').hasMatch(mac) || data is! Map ||
        data['mac'] != mac || data['commands'] is! List ||
        data['expires_in'] is! int || data['expires_in'] <= 0 || data['expires_in'] > 60) {
      throw const FormatException('Invalid private capabilities');
    }
    final commands = <int>{};
    for (final kind in data['commands']) {
      if (kind is! int || !{1,2,3,4,5,6,9,12,13,14,17,18,19,20,21,24,25,26}.contains(kind)) {
        throw const FormatException('Unsupported private capability');
      }
      commands.add(kind);
    }
    return PrivateControlPolicy._(mac, Set.unmodifiable(commands),
        (now ?? DateTime.now()).add(Duration(seconds: data['expires_in'])));
  }

  bool allows(String address, int kind, {DateTime? now}) =>
      address == mac && (kind == 17 ||
          (expires.isAfter(now ?? DateTime.now()) && commands.contains(kind)));

  static Future<PrivateControlPolicy> fetch(String mac, {Dio? dio}) async {
    final client = dio ?? Dio(BaseOptions(connectTimeout: const Duration(seconds: 10)));
    final uri = PrivateEndpoints.configured.management.resolve('private/v1/capabilities')
        .replace(queryParameters: {'mac': mac});
    final response = await client.getUri(uri, options: Options(
      headers: {'Authorization': PrivateSession.authorization(uri, 'management')},
      followRedirects: false, receiveTimeout: const Duration(seconds: 10),
    ));
    if (response.statusCode != 200 || response.data is! Map || response.data['code'] != 0) {
      throw StateError('Private capabilities unavailable');
    }
    return PrivateControlPolicy.parse(mac, response.data['data']);
  }
}
