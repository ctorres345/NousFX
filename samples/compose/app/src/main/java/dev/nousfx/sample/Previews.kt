// Previews of the sample screen for Via's capture: the default state, an empty state and long content.
// Via reads these (a public function marked @Preview, no parameters, at the top of the file) and never invents a state.
package dev.nousfx.sample

import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Preview(showBackground = true)
@Composable
fun HomePreview() {
    SampleTheme { HomeScreen() }
}

@Preview(showBackground = true)
@Composable
fun HomeEmptyPreview() {
    SampleTheme { HomeScreen(subtitle = "Nothing planned yet. Add something to get started.") }
}

@Preview(showBackground = true)
@Composable
fun HomeLongContentPreview() {
    SampleTheme {
        HomeScreen(
            title = "Good morning, and welcome back to your very long and detailed daily planner",
            subtitle = "Your plan for today has a great many things in it, more than fits on one line of a phone screen",
        )
    }
}
