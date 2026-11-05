import 'package:flutter_test/flutter_test.dart';
import 'package:stack_chan/network/private_session.dart';

void main() {
  tearDown(PrivateSession.clear);
  test('no private credentials falls closed', () {
    expect(() => PrivateSession.token('management'), throwsStateError);
  });
  test('credentials scoped by audience and origin', () {
    PrivateSession.install('management', 'synthetic-token',
        DateTime.now().add(const Duration(minutes: 2)));
    expect(PrivateSession.authorization(
        Uri.parse('https://management.example.invalid/stackChan/'), 'management'),
        'Bearer synthetic-token');
    expect(() => PrivateSession.authorization(
        Uri.parse('https://other.example.invalid/'), 'management'), throwsStateError);
    expect(() => PrivateSession.token('agent'), throwsStateError);
    PrivateSession.clear();
    expect(() => PrivateSession.token('management'), throwsStateError);
  });
  test('expired and long-lived credentials rejected', () {
    expect(() => PrivateSession.install('management', 'synthetic',
        DateTime.now().subtract(const Duration(seconds: 1))), throwsArgumentError);
    expect(() => PrivateSession.install('management', 'synthetic',
        DateTime.now().add(const Duration(days: 1))), throwsArgumentError);
  });
}
