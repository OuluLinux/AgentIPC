package org.oululinux.agentipc

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.io.File
import java.util.concurrent.Executors

/**
 * Minimal screen: connect over ssh to one node, log in, read a channel, post a line.
 * Not compiled or run yet; see android/README.md.
 */
class MainActivity : AppCompatActivity() {

    private val worker = Executors.newSingleThreadExecutor()
    private var transport: SshTransport? = null
    private var protocol: VsProtocol? = null

    private lateinit var host: EditText
    private lateinit var user: EditText
    private lateinit var server: EditText
    private lateinit var password: EditText
    private lateinit var channel: EditText
    private lateinit var text: EditText
    private lateinit var output: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        host = findViewById(R.id.host)
        user = findViewById(R.id.user)
        server = findViewById(R.id.server)
        password = findViewById(R.id.password)
        channel = findViewById(R.id.channel)
        text = findViewById(R.id.text)
        output = findViewById(R.id.output)

        findViewById<Button>(R.id.read).setOnClickListener { readChannel() }
        findViewById<Button>(R.id.say).setOnClickListener { sayLine() }
    }

    override fun onDestroy() {
        worker.execute { close() }
        worker.shutdown()
        super.onDestroy()
    }

    private fun readChannel() {
        withSession { p ->
            val messages = p.join(channel.text.toString())
            messages.joinToString("\n") { "[${it.ts}] ${it.user}: ${it.body}" }
                .ifEmpty { "(no messages)" }
        }
    }

    private fun sayLine() {
        withSession { p ->
            val seq = p.say(channel.text.toString(), text.text.toString())
            "posted [$seq]"
        }
    }

    /** Logs in if needed, runs the action on the worker thread, and shows the result. */
    private fun withSession(action: (VsProtocol) -> String) {
        worker.execute {
            val result = try {
                val p = protocol ?: connect()
                action(p)
            } catch (e: Exception) {
                close()
                "error: ${e.message}"
            }
            runOnUiThread { output.text = result }
        }
    }

    private fun connect(): VsProtocol {
        // Node and key paths are placeholders. The real app will take them from settings.
        val keyFile = File(filesDir, "id_ed25519")
        val knownHosts = File(filesDir, "known_hosts")
        val t = SshTransport(host.text.toString(), 22, user.text.toString(), keyFile, knownHosts)
        transport = t
        val (reader, writer) = t.open()
        val p = VsProtocol(reader, writer)
        p.login(user.text.toString(), server.text.toString(), password.text.toString())
        protocol = p
        return p
    }

    private fun close() {
        protocol?.quit()
        transport?.close()
        protocol = null
        transport = null
    }
}
