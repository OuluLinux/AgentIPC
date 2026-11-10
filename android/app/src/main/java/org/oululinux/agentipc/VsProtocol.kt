package org.oululinux.agentipc

import org.json.JSONObject
import java.io.BufferedReader
import java.io.Writer

/**
 * Client side of the virtual server line protocol, as in agentipc/client.py.
 *
 * Only plain (unencrypted) servers are handled. An encrypted server returns an
 * error before any message is read, because its bodies need libsodium.
 */
class VsException(message: String) : Exception(message)

data class VsMessage(val channel: String, val ts: String, val user: String, val body: String)

class VsProtocol(private val reader: BufferedReader, private val writer: Writer) {

    fun login(user: String, server: String, password: String) {
        send("LOGIN $user $server")
        val challenge = readLine().split(" ")
        if (challenge[0] != "CHALLENGE" || challenge.size != 4) {
            throw VsException(challenge.joinToString(" "))
        }
        val key = Auth.deriveKey(password, Auth.unhex(challenge[1]), challenge[2].toInt())
        send("PROOF " + Auth.proof(key, challenge[3]))
        val reply = readLine().split(" ")
        if (reply[0] != "OK" || reply.size != 4) {
            throw VsException(reply.joinToString(" "))
        }
        if (reply[3] == "1") {
            throw VsException("encrypted servers are not supported by this client yet")
        }
    }

    fun join(channel: String): List<VsMessage> {
        val name = normalise(channel)
        send("JOIN #$name")
        val history = mutableListOf<VsMessage>()
        while (true) {
            val line = readLine()
            when {
                line.startsWith("ERR") -> throw VsException(line)
                line.startsWith("MSG ") -> history.add(parseMessage(line.substring(4)))
                line == "ENDHIST #$name" -> return history
            }
        }
    }

    /** Posts one line. Returns the sequence number the server assigned. */
    fun say(channel: String, text: String): Int {
        require(!text.contains('\n') && !text.contains('\r')) { "message must be one line" }
        send("SAY #${normalise(channel)} $text")
        while (true) {
            val line = readLine()
            when {
                line.startsWith("ERR") -> throw VsException(line)
                line.startsWith("SENT ") -> return line.split(" ")[1].toInt()
            }
        }
    }

    fun quit() {
        runCatching { send("QUIT") }
    }

    private fun send(line: String) {
        writer.write(line + "\n")
        writer.flush()
    }

    private fun readLine(): String = reader.readLine() ?: throw VsException("connection closed")

    private fun parseMessage(json: String): VsMessage {
        val o = JSONObject(json)
        return VsMessage(
            channel = o.optString("chan"),
            ts = o.optString("ts"),
            user = o.optString("user"),
            body = o.optString("body"),
        )
    }

    companion object {
        fun normalise(channel: String): String = channel.trimStart('#').lowercase()
    }
}
