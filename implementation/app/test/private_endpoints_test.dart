import 'package:flutter_test/flutter_test.dart';
import 'package:stack_chan/network/private_endpoints.dart';

void main() {
  PrivateEndpoints make(String origin, {bool loopback = false}) =>
      PrivateEndpoints(management: origin, agent: origin, mcp: origin,
          allowLoopbackHttp: loopback);

  test('preserves API paths, port and derives secure websocket', () {
    final p = make('https://private.example.invalid:8443/');
    expect(p.api, 'https://private.example.invalid:8443/stackChan/');
    expect(p.control, 'wss://private.example.invalid:8443/stackChan/ws');
    expect(p.mcpList, 'https://private.example.invalid:8443/mcp/endpoints/list');
  });
  test('rejects insecure or credential-bearing origins', () {
    for (final origin in ['http://example.com', 'https://u:p@example.com',
      'https://example.com/path', 'https://example.com?token=x',
      'https://example.com#x', 'file:///tmp/test', 'example.com']) {
      expect(() => make(origin), throwsFormatException, reason: origin);
    }
  });
  test('HTTP exception is explicit and loopback only', () {
    expect(() => make('http://localhost'), throwsFormatException);
    expect(make('http://127.0.0.1:8080', loopback: true).control,
      'ws://127.0.0.1:8080/stackChan/ws');
    expect(() => make('http://192.0.2.1', loopback: true), throwsFormatException);
  });
}
