/// Private service origins. No implicit fallback to vendor infrastructure.
class PrivateEndpoints {
  PrivateEndpoints({
    required String management,
    required String agent,
    required String mcp,
    bool allowLoopbackHttp = false,
  }) : management = origin(management, allowLoopbackHttp),
       agent = origin(agent, allowLoopbackHttp),
       mcp = origin(mcp, allowLoopbackHttp);

  final Uri management;
  final Uri agent;
  final Uri mcp;

  static Uri origin(String value, bool allowLoopbackHttp) {
    final uri = Uri.parse(value);
    final loopback = uri.host == 'localhost' ||
        uri.host == '127.0.0.1' || uri.host == '::1';
    if (!uri.hasAuthority || uri.host.isEmpty || uri.userInfo.isNotEmpty ||
        uri.hasQuery || uri.hasFragment ||
        (uri.path.isNotEmpty && uri.path != '/') ||
        (uri.scheme != 'https' &&
         !(allowLoopbackHttp && loopback && uri.scheme == 'http'))) {
      throw FormatException('Expected an HTTPS origin without credentials or path');
    }
    return uri.replace(path: '/');
  }

  String get api => management.resolve('stackChan/').toString();
  String get files => management.toString();
  String get control => management.resolve('stackChan/ws')
      .replace(scheme: management.scheme == 'https' ? 'wss' : 'ws').toString();
  String get agentApi => agent.toString();
  String get mcpList => mcp.resolve('mcp/endpoints/list').toString();

  static final configured = PrivateEndpoints(
    management: const String.fromEnvironment('STACKCHAN_MANAGEMENT_ORIGIN',
        defaultValue: 'https://management.example.invalid'),
    agent: const String.fromEnvironment('STACKCHAN_AGENT_ORIGIN',
        defaultValue: 'https://agent.example.invalid'),
    mcp: const String.fromEnvironment('STACKCHAN_MCP_ORIGIN',
        defaultValue: 'https://mcp.example.invalid'),
    allowLoopbackHttp: const bool.fromEnvironment('STACKCHAN_ALLOW_LOOPBACK_HTTP'),
  );
}
