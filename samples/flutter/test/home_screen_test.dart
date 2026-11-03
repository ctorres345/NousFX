import 'package:flutter_test/flutter_test.dart';
import 'package:via_sample_flutter/main.dart';

void main() {
  testWidgets('home screen shows the greeting and the start button', (tester) async {
    await tester.pumpWidget(const SampleApp());
    expect(find.text('Good morning'), findsOneWidget);
    expect(find.text('Start'), findsOneWidget);
  });
}
