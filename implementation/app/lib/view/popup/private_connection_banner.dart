import 'package:flutter/cupertino.dart';
import 'package:flutter/foundation.dart';

class PrivateConnectionBanner extends StatelessWidget {
  const PrivateConnectionBanner({super.key, required this.status});
  final ValueListenable<String> status;

  @override
  Widget build(BuildContext context) => SafeArea(bottom: false,
    child: Padding(padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: ValueListenableBuilder<String>(valueListenable: status,
        builder: (context, text, child) => Text(text, textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 13, color: CupertinoColors.systemGrey)),
      ),
    ),
  );
}
