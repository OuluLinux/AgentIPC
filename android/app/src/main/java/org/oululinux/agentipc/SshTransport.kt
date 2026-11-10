package org.oululinux.agentipc

import net.schmizz.sshj.SSHClient
import net.schmizz.sshj.connection.channel.direct.Session
import java.io.BufferedReader
import java.io.Closeable
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.io.Writer

/**
 * One ssh connection to a mesh node, running `agentipc bridge client` there.
 *
 * ssh provides the encryption; nothing here adds transport crypto.
 * Host keys are checked against a known_hosts file the app controls.
 */
class SshTransport(
    private val host: String,
    private val port: Int,
    private val user: String,
    private val keyFile: File,
    private val knownHosts: File,
) : Closeable {

    private val ssh = SSHClient()
    private var session: Session? = null
    private var command: Session.Command? = null

    /** Opens the ssh connection and the remote bridge. Returns the line protocol streams. */
    fun open(): Pair<BufferedReader, Writer> {
        ssh.loadKnownHosts(knownHosts)
        ssh.connect(host, port)
        ssh.authPublickey(user, ssh.loadKeys(keyFile.absolutePath))
        val s = ssh.startSession()
        val cmd = s.exec(REMOTE_COMMAND)
        session = s
        command = cmd
        val reader = BufferedReader(InputStreamReader(cmd.inputStream, Charsets.UTF_8))
        val writer = OutputStreamWriter(cmd.outputStream, Charsets.UTF_8)
        return reader to writer
    }

    override fun close() {
        runCatching { command?.close() }
        runCatching { session?.close() }
        runCatching { ssh.disconnect() }
    }

    companion object {
        const val REMOTE_COMMAND = "agentipc bridge client"
    }
}
