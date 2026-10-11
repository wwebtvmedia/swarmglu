package com.tree4five.swarmglu

import java.io.File

/**
 * Point d'entrée du nœud Kotlin SwarmGlu (interopérable avec les nœuds Python).
 *
 *   bin/swarmglu-kotlin --node-id kot-a --port 7201 --peers 127.0.0.1:7901
 */
fun main(args: Array<String>) {
    var nodeId = ""; var port = 7001; var ip = ""; var portAnnonce = 0
    var mode = "full"; var essaim = "salle-kotlin"
    var pairs = listOf<String>(); var hb = 3.0; var bail = 6.0
    var dossier = "data/kotlin"; var whitelist = "whitelist-kotlin.json"
    var i = 0
    while (i < args.size) {
        when (args[i]) {
            "--node-id" -> nodeId = args[++i]
            "--port" -> port = args[++i].toInt()
            "--advertise-ip" -> ip = args[++i]
            "--advertise-port" -> portAnnonce = args[++i].toInt()
            "--mode" -> mode = args[++i]
            "--swarm-id" -> essaim = args[++i]
            "--peers" -> pairs = args[++i].split(",").map { it.trim() }.filter { it.isNotEmpty() }
            "--heartbeat" -> hb = args[++i].toDouble()
            "--lease" -> bail = args[++i].toDouble()
            "--data-dir" -> dossier = args[++i]
            "--whitelist-file" -> whitelist = args[++i]
        }
        i++
    }
    if (nodeId.isEmpty()) nodeId = "kotlin-$port"
    if (ip.isEmpty()) ip = ipLocale()
    if (portAnnonce == 0) portAnnonce = port
    File(dossier).mkdirs()

    val noeud = NoeudKotlin(
        nodeId, port, ip, portAnnonce, mode, essaim,
        dossier, whitelist, ConsensusConfig(hb, bail))
    // Graines statiques (« ip:port ») : entrées de registre marquées seed-…
    val etat = noeud.etat
    for (graine in pairs) {
        val (h, p) = graine.substringBeforeLast(":") to graine.substringAfterLast(":").toInt()
        etat.majPair("seed-$graine", mapOf("ip" to h, "port" to p))
        etat.toucher("seed-$graine")
    }
    noeud.demarrer()
    println("Nœud Kotlin '$nodeId' démarré sur $ip:$portAnnonce (essaim '$essaim')")
    Thread.currentThread().join()
}

fun ipLocale(): String = try {
    java.net.NetworkInterface.getNetworkInterfaces().asSequence()
        .filter { it.isUp && !it.isLoopback }
        .flatMap { it.inetAddresses.asSequence() }
        .firstOrNull { it is java.net.Inet4Address && !it.isLoopbackAddress }
        ?.hostAddress ?: "127.0.0.1"
} catch (_: Exception) {
    "127.0.0.1"
}
