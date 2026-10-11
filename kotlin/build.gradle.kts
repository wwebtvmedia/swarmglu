// SwarmGlu — noyau Kotlin/JVM (portage v2.0 du cœur Python)
//
// OBJECTIF D'INVARIANTE : interopérabilité CÂBLE avec les nœuds Python —
// même élection déterministe, même chaîne d'audit (hash pour hash), même
// fusion LWW. Prouvé par des vecteurs « golden » générés depuis Python
// (src/test/resources/golden.json) et un essaim mixte réel
// (tests/test_kotlin_interop.py côté dépôt).
//
// Zéro dépendance Android ; JSON parsé avec org.json (léger, stable),
// serveur HTTP via le JDK (com.sun.net.httpserver).

plugins {
    kotlin("jvm") version "1.9.24"
    application
}

group = "com.tree4five"
version = "0.1.0"

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.json:json:20240303")
    testImplementation(kotlin("test"))
}

kotlin {
    jvmToolchain(21)
}

application {
    mainClass.set("com.tree4five.swarmglu.MainKt")
}

tasks.test {
    useJUnitPlatform()
}

// Distribution exécutable : build/install/swarmglu-kotlin/bin/…
// (utilisée par tests/test_kotlin_interop.py pour lancer le nœud mixte)
tasks.register<Copy>("distBin") {
    dependsOn("installDist")
    from(layout.buildDirectory.dir("install/swarmglu-kotlin/bin"))
    into(layout.buildDirectory.dir("bin"))
}
