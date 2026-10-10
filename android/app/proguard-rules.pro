# SwarmGlu — règles ProGuard/R8.
# Le code Python embarqué n'est pas concerné par la minification ;
# minifyEnabled est désactivé par défaut pour le PoC école.

# Si vous activez minifyEnabled, conservez l'API JNI de Chaquopy :
-keep class com.chaquo.python.** { *; }
