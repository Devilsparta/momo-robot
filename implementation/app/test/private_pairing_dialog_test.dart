import 'dart:async';
import 'package:flutter/cupertino.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:stack_chan/view/popup/private_pairing_dialog.dart';

void main() {
  testWidgets('requires six digits, confirms once and waits for server', (tester) async {
    final result = Completer<bool>();
    var calls = 0;
    String? received;
    await tester.pumpWidget(CupertinoApp(home: Builder(builder: (context) =>
      CupertinoButton(child: const Text('open'), onPressed: () =>
        showCupertinoDialog<bool>(context: context, builder: (_) => PrivatePairingDialog(
          deviceMac: '020000000001', confirm: (code) { calls++; received = code; return result.future; }))))));
    await tester.tap(find.text('open')); await tester.pumpAndSettle();
    await tester.tap(find.byKey(const Key('confirm-pairing'))); await tester.pump();
    expect(calls, 0);
    await tester.enterText(find.byKey(const Key('pairing-code')), '123456');
    await tester.tap(find.byKey(const Key('confirm-pairing'))); await tester.pump();
    await tester.tap(find.byKey(const Key('confirm-pairing'))); await tester.pump();
    expect(calls, 1); expect(received, '123456');
    result.complete(true); await tester.pumpAndSettle();
    expect(find.byType(PrivatePairingDialog), findsNothing);
  });

  testWidgets('rejection stays open without exposing server details', (tester) async {
    await tester.pumpWidget(CupertinoApp(home: PrivatePairingDialog(
      deviceMac: '020000000001', confirm: (_) async => throw StateError('private-token'))));
    await tester.enterText(find.byKey(const Key('pairing-code')), '123456');
    await tester.tap(find.byKey(const Key('confirm-pairing'))); await tester.pumpAndSettle();
    expect(find.byType(PrivatePairingDialog), findsOneWidget);
    expect(find.textContaining('private-token'), findsNothing);
    expect(find.textContaining('无法确认'), findsOneWidget);
  });
}
