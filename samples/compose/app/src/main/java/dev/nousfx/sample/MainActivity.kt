package dev.nousfx.sample

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MaterialTheme { HomeScreen() } }
    }
}

// Sample screen for Via's tests. It has two deliberate design flaws for audits to find:
// the subtitle has weak contrast, and the button is smaller than the 48dp touch target.
@Composable
fun HomeScreen() {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Good morning", style = MaterialTheme.typography.headlineMedium)
        Text("Your plan for today", color = Color(0xFFBBBBBB), fontSize = 14.sp)
        Button(onClick = {}, modifier = Modifier.size(width = 96.dp, height = 28.dp)) {
            Text("Start", fontSize = 12.sp)
        }
    }
}

@Preview(showBackground = true)
@Composable
fun HomeScreenPreview() {
    MaterialTheme { HomeScreen() }
}
