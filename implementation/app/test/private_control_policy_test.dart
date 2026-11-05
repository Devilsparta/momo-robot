import 'dart:typed_data';
import 'package:dio/dio.dart';
import 'package:flutter/cupertino.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stack_chan/network/private_control_policy.dart';
import 'package:stack_chan/network/private_session.dart';
import 'package:stack_chan/view/popup/private_connection_banner.dart';

class PolicyAdapter implements HttpClientAdapter {
  RequestOptions? request;
  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? stream,
      Future<void>? cancelFuture) async {
    request = options;
    return ResponseBody.fromString('{"code":0,"data":{"mac":"020000000001","commands":[3,4,17],"expires_in":60}}', 200,
        headers: {Headers.contentTypeHeader: ['application/json']});
  }
  @override
  void close({bool force = false}) {}
}

void main() {
  tearDown(() { PrivateSession.clear(); PrivateControlPolicy.active = null; });
  test('capabilities expire and cannot cross devices or enable absent media', () {
    final now = DateTime.utc(2026, 11, 1);
    final policy = PrivateControlPolicy.parse('020000000001', {
      'mac':'020000000001', 'commands':[3,4,17], 'expires_in':60,
    }, now: now);
    expect(policy.allows('020000000001', 3, now: now), true);
    expect(policy.allows('020000000002', 3, now: now), false);
    expect(policy.allows('020000000001', 5, now: now), false);
    expect(PrivateControlPolicy.mediaAllowed(18), false);
    expect(PrivateControlPolicy.mediaAllowed(9), false);
    expect(policy.allows('020000000001', 3, now: now.add(const Duration(seconds: 60))), false);
    expect(() => PrivateControlPolicy.parse('020000000001', {
      'mac':'020000000002', 'commands':[3], 'expires_in':60,
    }), throwsFormatException);
    expect(() => PrivateControlPolicy.parse('020000000001', {
      'mac':'020000000001', 'commands':[27], 'expires_in':60,
    }), throwsFormatException);
  });
  test('logout immediately invalidates an otherwise live capture capability', () {
    PrivateSession.install('management','synthetic',DateTime.now().add(const Duration(minutes: 2)));
    PrivateControlPolicy.active = PrivateControlPolicy.parse('020000000001', {
      'mac':'020000000001', 'commands':[9,18], 'expires_in':60,
    });
    expect(PrivateControlPolicy.mediaAllowed(18), true);
    expect(PrivateControlPolicy.mediaAllowed(9), true);
    PrivateSession.clear();
    expect(PrivateControlPolicy.mediaAllowed(18), false);
    expect(PrivateControlPolicy.mediaAllowed(9), false);
  });
  test('capability lookup authenticates only the management audience', () async {
    final adapter = PolicyAdapter();
    PrivateSession.install('management','synthetic',DateTime.now().add(const Duration(minutes: 2)));
    final policy = await PrivateControlPolicy.fetch('020000000001',dio:Dio()..httpClientAdapter=adapter);
    expect(policy.allows('020000000001', 3), true);
    expect(adapter.request!.uri.path,'/private/v1/capabilities');
    expect(adapter.request!.headers['Authorization'],'Bearer synthetic');
    expect(adapter.request!.followRedirects,false);
  });
  testWidgets('private connection state is visible and updates', (tester) async {
    final status=ValueNotifier('连接中');
    addTearDown(status.dispose);
    await tester.pumpWidget(CupertinoApp(home:PrivateConnectionBanner(status:status)));
    expect(find.text('连接中'),findsOneWidget);
    status.value='会话已过期';await tester.pump();
    expect(find.text('会话已过期'),findsOneWidget);
  });
}
