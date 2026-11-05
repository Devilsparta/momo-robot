import 'dart:typed_data';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stack_chan/network/private_pairing.dart';
import 'package:stack_chan/network/private_session.dart';

class CaptureAdapter implements HttpClientAdapter {
  RequestOptions? request;
  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? stream,
      Future<void>? cancelFuture) async {
    request = options;
    return ResponseBody.fromString('{"code":0,"data":true}', 200,
        headers: {Headers.contentTypeHeader: ['application/json']});
  }
  @override
  void close({bool force = false}) {}
}

void main() {
  tearDown(PrivateSession.clear);
  test('bind sends selected MAC and code using authenticated private user', () async {
    final adapter = CaptureAdapter();
    final dio = Dio()..httpClientAdapter = adapter;
    PrivateSession.install('management', 'synthetic',
        DateTime.now().add(const Duration(minutes: 2)));
    expect(await PrivatePairingClient(dio: dio).confirm('02:00:00:00:00:01', '123456'), true);
    expect(adapter.request!.uri.toString(), 'https://management.example.invalid/private/v1/bind');
    expect(adapter.request!.data, {'mac':'020000000001','code':'123456'});
    expect(adapter.request!.headers['Authorization'], 'Bearer synthetic');
    expect(adapter.request!.followRedirects, false);
  });
  test('missing session cannot send a code or fall back to vendor credentials', () async {
    final adapter = CaptureAdapter();
    final dio = Dio()..httpClientAdapter = adapter;
    await expectLater(PrivatePairingClient(dio: dio).confirm('020000000001', '123456'),
        throwsStateError);
    expect(adapter.request, null);
  });
}
