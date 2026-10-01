#!/usr/bin/env bash
# Para o rotulador (5010) e o servidor de IA (5051) pelos PIDs que escutam nas portas.
for porta in 5010 5051; do
  for pid in $(netstat -ano | grep -E "127.0.0.1:$porta .*LISTENING" | awk '{print $5}' | sort -u); do
    taskkill //PID "$pid" //T //F
  done
done
