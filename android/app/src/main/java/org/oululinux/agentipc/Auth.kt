package org.oululinux.agentipc

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Password key derivation and login proof, matching agentipc/auth.py.
 *
 * PBKDF2 is written out here rather than taken from javax.crypto.PBEKeySpec,
 * because PBEKeySpec keeps only the low byte of each character. Python hashes
 * the UTF-8 bytes of the password, and so does this code.
 */
object Auth {
    private const val HMAC = "HmacSHA256"
    private const val HASH_LEN = 32

    fun deriveKey(password: String, salt: ByteArray, iterations: Int): ByteArray {
        val mac = Mac.getInstance(HMAC)
        mac.init(SecretKeySpec(password.toByteArray(Charsets.UTF_8), HMAC))
        // One block is enough: the key is 32 bytes and SHA-256 gives 32 bytes per block.
        mac.update(salt)
        mac.update(byteArrayOf(0, 0, 0, 1))
        var u = mac.doFinal()
        val out = u.copyOf()
        for (i in 1 until iterations) {
            u = mac.doFinal(u)
            for (j in 0 until HASH_LEN) {
                out[j] = (out[j].toInt() xor u[j].toInt()).toByte()
            }
        }
        return out
    }

    /** HMAC-SHA256 of the nonce text (ASCII), as lowercase hex. */
    fun proof(key: ByteArray, nonce: String): String {
        val mac = Mac.getInstance(HMAC)
        mac.init(SecretKeySpec(key, HMAC))
        return mac.doFinal(nonce.toByteArray(Charsets.US_ASCII)).toHex()
    }

    fun unhex(text: String): ByteArray {
        require(text.length % 2 == 0) { "odd hex length" }
        return ByteArray(text.length / 2) { i ->
            text.substring(i * 2, i * 2 + 2).toInt(16).toByte()
        }
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
}
