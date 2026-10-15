package com.david.homeenv

import android.util.Base64
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * The backend's REST API (contracts/api.md): one HTTP client, base URL and Basic auth
 * for the home screen, the widget and the push registration. All calls block: run them
 * off the main thread. Failures are logged and returned as null, never thrown.
 */
object Api {
    private val client = OkHttpClient()
    private val authHeader = "Basic " + Base64.encodeToString(
        "${BuildConfig.API_USER}:${BuildConfig.API_PASS}".toByteArray(), Base64.NO_WRAP
    )
    private val JSON = "application/json; charset=utf-8".toMediaType()

    private fun url(path: String) = "${BuildConfig.API_BASE_URL}$path"

    /** GET [path]: the body of a 2xx response, else null. [timeoutS] caps the whole call. */
    fun get(path: String, timeoutS: Long? = null): String? = try {
        execute(Request.Builder().url(url(path)).get(), timeoutS).use { if (it.isSuccessful) it.body?.string() else null }
    } catch (e: Exception) {
        e.printStackTrace()
        null
    }

    // Unexpected bodies (e.g. an HTML error page) become null instead of an exception
    fun getObject(path: String, timeoutS: Long? = null): JSONObject? =
        get(path, timeoutS)?.let { runCatching { JSONObject(it) }.getOrNull() }

    fun getArray(path: String, timeoutS: Long? = null): JSONArray? =
        get(path, timeoutS)?.let { runCatching { JSONArray(it) }.getOrNull() }

    /** POST [body] as JSON to [path]: the HTTP status, or null if the request failed. */
    fun post(path: String, body: JSONObject): Int? = try {
        execute(Request.Builder().url(url(path)).post(body.toString().toRequestBody(JSON)), null).use { it.code }
    } catch (e: Exception) {
        e.printStackTrace()
        null
    }

    /** POST [body] as JSON to [path]: the JSON object of a 2xx response, else null. */
    fun postForObject(path: String, body: JSONObject): JSONObject? = try {
        execute(Request.Builder().url(url(path)).post(body.toString().toRequestBody(JSON)), null).use {
            if (it.isSuccessful) it.body?.string()?.let { s -> runCatching { JSONObject(s) }.getOrNull() } else null
        }
    } catch (e: Exception) {
        e.printStackTrace()
        null
    }

    private fun execute(request: Request.Builder, timeoutS: Long?): Response {
        val c = if (timeoutS == null) client else client.newBuilder().callTimeout(timeoutS, TimeUnit.SECONDS).build()
        return c.newCall(request.addHeader("Authorization", authHeader).build()).execute()
    }
}

/** [key] as a number, or null when it is missing, JSON null or not a number. */
fun JSONObject.optNum(key: String): Double? =
    if (has(key) && !isNull(key)) optDouble(key).takeUnless { it.isNaN() } else null
