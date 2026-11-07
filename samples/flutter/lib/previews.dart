// Previews of the sample screen for Via's capture: the default state, an empty state and long content.
// Via reads these (a public function marked @Preview, no parameters) and never invents a state of its own.
import 'package:flutter/material.dart';
import 'package:flutter/widget_previews.dart';

import 'main.dart';

Widget _app(Widget home) => MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.indigo),
      darkTheme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.indigo, brightness: Brightness.dark),
      home: home,
    );

@Preview(name: 'Home')
Widget homePreview() => _app(const HomeScreen());

@Preview(name: 'Home empty')
Widget homeEmptyPreview() => _app(const HomeScreen(subtitle: 'Nothing planned yet. Add something to get started.'));

@Preview(name: 'Home long content')
Widget homeLongContentPreview() => _app(const HomeScreen(
      title: 'Good morning, and welcome back to your very long and detailed daily planner',
      subtitle: 'Your plan for today has a great many things in it, more than fits on one line of a phone screen',
    ));
