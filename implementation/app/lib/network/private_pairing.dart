import 'package:dio/dio.dart';
import 'private_endpoints.dart';
import 'private_session.dart';

/// Uses an existing authenticated private user session; never accepts MAC as auth.
class PrivatePairingClient {
  PrivatePairingClient({Dio? dio}) : _dio = dio ?? Dio(BaseOptions(connectTimeout: const Duration(seconds: 10)));
  final Dio _dio;

  Future<bool> confirm(String mac, String code) async {
    final address = mac.replaceAll(':', '').replaceAll('-', '').toLowerCase();
    if (!RegExp(r'^[0-9a-f]{12}$').hasMatch(address) ||
        !RegExp(r'^[0-9]{6}$').hasMatch(code)) {
      throw const FormatException('Invalid device or code');
    }
    final uri = PrivateEndpoints.configured.management.resolve('private/v1/bind');
    final response = await _dio.postUri(uri,
      data: {'mac': address, 'code': code},
      options: Options(
        headers: {'Authorization': PrivateSession.authorization(uri, 'management')},
        followRedirects: false,
        sendTimeout: const Duration(seconds: 10),
        receiveTimeout: const Duration(seconds: 10),
      ));
    return response.statusCode == 200 && response.data is Map &&
        response.data['code'] == 0 && response.data['data'] == true;
  }
}
