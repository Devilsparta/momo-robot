import 'private_endpoints.dart';

/// In-memory short-lived credentials only. Bootstrap/login UI is not implemented.
/// Never put these credentials in dart-define, source, or persistent preferences.
class PrivateSession {
  static const enabled = bool.fromEnvironment(
      'STACKCHAN_PRIVATE_BACKEND', defaultValue: true);
  static final Map<String, ({String token, DateTime expires})> _grants = {};

  static void install(String audience, String token, DateTime expires) {
    if (!['management', 'agent', 'mcp'].contains(audience) ||
        token.isEmpty || RegExp(r'\s').hasMatch(token) ||
        !expires.isAfter(DateTime.now()) ||
        expires.difference(DateTime.now()) > const Duration(hours: 1)) {
      throw ArgumentError('Invalid short-lived private session');
    }
    _grants[audience] = (token: token, expires: expires);
  }

  static void clear() => _grants.clear();

  static String token(String audience) {
    final grant = _grants[audience];
    if (grant == null || !grant.expires.isAfter(DateTime.now())) {
      _grants.remove(audience);
      throw StateError('Private sign-in required; legacy credentials are disabled');
    }
    return grant.token;
  }

  static String authorization(Uri target, String audience) {
    final endpoints = PrivateEndpoints.configured;
    final expected = switch (audience) {
      'management' => endpoints.management,
      'agent' => endpoints.agent,
      'mcp' => endpoints.mcp,
      _ => throw ArgumentError('Unknown credential audience'),
    };
    final scheme = switch (target.scheme) {
      'wss' => 'https',
      'ws' => 'http',
      _ => target.scheme,
    };
    final canonical = target.replace(scheme: scheme);
    if (canonical.origin != expected.origin || target.userInfo.isNotEmpty) {
      throw StateError('Refusing credential forwarding to a different origin');
    }
    return 'Bearer ${token(audience)}';
  }
}
