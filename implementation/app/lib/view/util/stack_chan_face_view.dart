/*
SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
SPDX-License-Identifier: MIT
*/

import 'dart:typed_data';
import 'dart:async';
import '../../network/private_control_policy.dart';

import 'package:camera/camera.dart';
import 'package:cupertino_ui/cupertino_ui.dart';
import 'package:google_mlkit_face_detection/google_mlkit_face_detection.dart';
import 'package:opencv_dart/opencv_dart.dart' as cv;
import 'package:stack_chan/model/expression_data.dart';
import 'package:stack_chan/util/ml_kit_util.dart';

import '../../model/dance_list.dart';

class StackChanFaceView extends StatefulWidget {
  const StackChanFaceView({
    super.key,
    required this.captureScreen,
    this.onFrameCallback,
    this.onCallback,
  });

  final bool captureScreen; // Whether camera frames are exported
  final Function(Uint8List)? onFrameCallback; // Receives each exported frame
  final Function(DanceData)? onCallback; // Receives the detected face data

  @override
  State<StatefulWidget> createState() => _StackChanFaceViewState();
}

class _StackChanFaceViewState extends State<StackChanFaceView> {
  CameraController? cameraController;
  Timer? _permissionWatch;

  DateTime lastProcessTime = DateTime.now();
  bool isProcessing = false;

  @override
  void initState() {
    super.initState();
    initCamera();
  }

  Future<void> _stopPrivateCamera() async {
    final controller = cameraController;
    cameraController = null;
    _permissionWatch?.cancel();
    _permissionWatch = null;
    if (mounted) setState(() {});
    if (controller != null) await controller.dispose();
  }

  Future<void> initCamera() async {
    if (!PrivateControlPolicy.mediaAllowed(18)) return;
    final cameras = await availableCameras();
    if (!mounted || !PrivateControlPolicy.mediaAllowed(18)) return;
    final fronts = cameras.where((camera) => camera.lensDirection == CameraLensDirection.front);
    if (fronts.isEmpty) return;
    final front = fronts.first;
    final controller = CameraController(front, ResolutionPreset.medium,
        enableAudio: false, imageFormatGroup: ImageFormatGroup.nv21);
    cameraController = controller;
    try {
      await controller.initialize();
      if (!mounted || cameraController != controller || !PrivateControlPolicy.mediaAllowed(18)) {
        if (cameraController == controller) await _stopPrivateCamera();
        return;
      }
      await controller.startImageStream((image) {
        if (!PrivateControlPolicy.mediaAllowed(18)) {
          unawaited(_stopPrivateCamera());
          return;
        }
        processCameraImage(image, front.sensorOrientation);
      });
      if (!mounted || cameraController != controller || !PrivateControlPolicy.mediaAllowed(18)) {
        if (cameraController == controller) await _stopPrivateCamera();
        return;
      }
      _permissionWatch = Timer.periodic(const Duration(milliseconds: 500), (_) {
        if (!PrivateControlPolicy.mediaAllowed(18)) unawaited(_stopPrivateCamera());
      });
      if (mounted) setState(() {});
    } on CameraException {
      if (cameraController == controller) await _stopPrivateCamera();
    }
  }

  /// Detect faces and export camera frames
  void processCameraImage(CameraImage image, int sensorOrientation) {
    if (!PrivateControlPolicy.mediaAllowed(18)) return;
    // Detect faces in the current frame
    MlKitUtil.shared.testing(image, sensorOrientation, (faces) {
      if (faces.isNotEmpty) {
        dataConversionTesting(faces.first);
      }
    });
    if (widget.captureScreen) {
      // Compress and hand off the frame asynchronously
      final now = DateTime.now();
      if (now.difference(lastProcessTime).inMilliseconds >= 100) {
        if (isProcessing) return;
        lastProcessTime = now;
        isProcessing = true;
        handleAsyncCompression(image, sensorOrientation);
      }
    }
  }

  /// Convert face tracking data into a dance frame
  void dataConversionTesting(Face face) {
    double headYaw = face.headEulerAngleY ?? 0;
    double headPitch = face.headEulerAngleX ?? 0;

    int yawServoAngle = (headYaw * -20).toInt().clamp(-1280, 1280);
    int pitchServoAngle = (headPitch * 10).toInt().clamp(0, 900);

    double leftEyeProb = face.leftEyeOpenProbability ?? 1.0;
    double rightEyeProb = face.rightEyeOpenProbability ?? 1.0;

    int leftWeight = (leftEyeProb * 100).toInt().clamp(0, 100);
    int rightWeight = (rightEyeProb * 100).toInt().clamp(0, 100);

    double smileProb = face.smilingProbability ?? 0.0;
    int mouthWeight = (smileProb * 100).toInt().clamp(0, 100);

    ExpressionItem leftEye = ExpressionItem(
      x: 0,
      y: 0,
      rotation: 0,
      weight: leftWeight,
    );
    ExpressionItem rightEye = ExpressionItem(
      x: 0,
      y: 0,
      rotation: 0,
      weight: rightWeight,
    );
    ExpressionItem mouth = ExpressionItem(
      x: 0,
      y: 0,
      rotation: 0,
      weight: mouthWeight,
    );

    if (smileProb > 0.3) {
      leftEye.weight = (leftEye.weight - 35).clamp(0, 100);
      leftEye.rotation = -2150;
      rightEye.weight = (rightEye.weight - 35).clamp(0, 100);
      rightEye.rotation = 2150;
    } else if (smileProb < 0.1 && (leftEyeProb < 0.5 || rightEyeProb < 0.5)) {
      leftEye.rotation = 450;
      rightEye.rotation = -450;
    }

    DanceData data = DanceData(
      leftEye: leftEye,
      rightEye: rightEye,
      mouth: mouth,
      yawServo: MotionDataItem(angle: yawServoAngle),
      pitchServo: MotionDataItem(angle: pitchServoAngle),
      durationMs: 1000,
    );

    if (mounted && PrivateControlPolicy.mediaAllowed(18) && widget.onCallback != null) {
      widget.onCallback!(data);
    }
  }

  /// Encode the frame as JPEG and emit it
  Future<void> handleAsyncCompression(
    CameraImage image,
    int sensorOrientation,
  ) async {
    final nv21Bytes = image.planes.first.bytes;
    final mat = cv.Mat.fromList(
      (image.height * 1.5).toInt(),
      image.width,
      .CV_8UC1,
      nv21Bytes,
    );
    final bgrMat = cv.cvtColor(mat, cv.COLOR_YUV2BGR_NV21);
    final rotatedMat = rotateMatIfNeeded(bgrMat, sensorOrientation);
    final (success, jpegByte) = cv.imencode(".jpg", rotatedMat);
    if (success) {
      if (mounted && PrivateControlPolicy.mediaAllowed(18) && jpegByte.isNotEmpty && widget.onFrameCallback != null) {
        widget.onFrameCallback!(jpegByte);
      }
    }
    mat.dispose();
    bgrMat.dispose();
    rotatedMat.dispose();
    isProcessing = false;
  }

  cv.Mat rotateMatIfNeeded(cv.Mat src, int orientation) {
    if (orientation == 90) {
      return cv.rotate(src, cv.ROTATE_90_CLOCKWISE);
    } else if (orientation == 180) {
      return cv.rotate(src, cv.ROTATE_180);
    } else if (orientation == 270) {
      return cv.rotate(src, cv.ROTATE_90_COUNTERCLOCKWISE);
    }
    return src;
  }

  @override
  void dispose() {
    _permissionWatch?.cancel();
    cameraController?.dispose();
    cameraController = null;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!PrivateControlPolicy.mediaAllowed(18)) {
      return const Center(child: Text('手机相机尚未获准启用'));
    }
    if (cameraController == null || !cameraController!.value.isInitialized) {
      return const Center(child: CupertinoActivityIndicator());
    } else {
      return SizedBox.expand(child: CameraPreview(cameraController!));
    }
  }
}
