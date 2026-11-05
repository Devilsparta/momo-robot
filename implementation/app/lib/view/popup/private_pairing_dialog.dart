import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart';

class PrivatePairingDialog extends StatefulWidget {
  const PrivatePairingDialog({super.key, required this.deviceMac, required this.confirm});
  final String deviceMac;
  final Future<bool> Function(String code) confirm;
  @override
  State<PrivatePairingDialog> createState() => _PrivatePairingDialogState();
}

class _PrivatePairingDialogState extends State<PrivatePairingDialog> {
  final _code = TextEditingController();
  bool _busy = false;
  String? _error;
  @override
  void dispose() { _code.dispose(); super.dispose(); }

  Future<void> _confirm() async {
    if (_busy) return;
    if (!RegExp(r'^[0-9]{6}$').hasMatch(_code.text)) {
      setState(() => _error = '请输入设备屏幕上的 6 位数字');
      return;
    }
    setState(() { _busy = true; _error = null; });
    try {
      final accepted = await widget.confirm(_code.text);
      if (!mounted) return;
      if (accepted) {
        Navigator.of(context).pop(true);
        return;
      }
      setState(() => _error = '绑定未完成，请核对设备和一次性码');
    } catch (_) {
      if (mounted) setState(() => _error = '无法确认。请检查登录、网络或重新获取一次性码');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !_busy,
    child: CupertinoAlertDialog(
      title: const Text('确认绑定机器人'),
      content: Column(children: [
        const SizedBox(height: 8),
        Text('设备：${widget.deviceMac}\n在机器人 SETUP → Private pairing 中获取一次性码。'),
        const SizedBox(height: 12),
        CupertinoTextField(
          key: const Key('pairing-code'),
          controller: _code, enabled: !_busy,
          placeholder: '6 位一次性码',
          keyboardType: TextInputType.number,
          autofillHints: const [AutofillHints.oneTimeCode],
          inputFormatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(6)],
          onSubmitted: (_) => _confirm(),
        ),
        if (_busy) const Padding(padding: EdgeInsets.all(8), child: CupertinoActivityIndicator()),
        if (_error != null) Padding(padding: const EdgeInsets.only(top: 8),
          child: Text(_error!, style: const TextStyle(color: CupertinoColors.destructiveRed))),
      ]),
      actions: [
        CupertinoDialogAction(onPressed: _busy ? null : () => Navigator.of(context).pop(false),
          child: const Text('取消')),
        CupertinoDialogAction(key: const Key('confirm-pairing'), isDefaultAction: true,
          onPressed: _busy ? null : _confirm, child: const Text('确认绑定')),
      ],
    ),
  );
}
