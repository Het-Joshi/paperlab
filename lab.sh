#!/usr/bin/env bash
# lab.sh — start/stop paperlab's services on demand (nothing runs at boot).
#   ./lab.sh up        # Ollama only  -> reviews, brainstorm, corpus
#   ./lab.sh up-web    # Ollama + SearXNG -> adds Web search + Fact-check
#   ./lab.sh down      # stop everything
set -e
OS="$(uname -s)"
have(){ command -v "$1" >/dev/null 2>&1; }

ollama_up(){
  if curl -s http://localhost:11434/api/version >/dev/null 2>&1; then
    echo "ollama  : already running"; return; fi
  if have systemctl; then sudo systemctl start ollama
  else ( ollama serve >/tmp/paperlab-ollama.log 2>&1 & ); sleep 2; fi
  echo "ollama  : up"
}
ollama_down(){
  if have systemctl; then sudo systemctl stop ollama 2>/dev/null || true
  else pkill -f "ollama serve" 2>/dev/null || true; fi
  echo "ollama  : down"
}

# prefer podman (daemonless -> nothing at boot, no docker/containerd services)
engine(){ if have podman; then echo podman; elif have docker; then echo docker; fi; }

docker_daemon_up(){   # only needed when the engine is docker
  docker info >/dev/null 2>&1 && return 0
  if have systemctl; then
    sudo systemctl reset-failed containerd docker.service docker.socket 2>/dev/null || true
    sudo systemctl start containerd && sudo systemctl start docker
  elif [ "$OS" = "Darwin" ]; then
    open -a Docker; printf "docker  : waiting for Desktop"
    for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; printf "."; sleep 2; done; echo
  fi
}
searxng_up(){
  local e; e="$(engine)"
  [ -z "$e" ] && { echo "searxng : no podman/docker (try: sudo apt install -y podman)"; return 0; }
  [ "$e" = docker ] && { docker_daemon_up || return 0; }
  "$e" start searxng >/dev/null 2>&1 \
    || "$e" run -d --name searxng -p 8080:8080 docker.io/searxng/searxng >/dev/null
  echo "searxng : up via $e (http://localhost:8080)"
}
searxng_down(){
  local e; e="$(engine)"
  [ -n "$e" ] && "$e" stop searxng >/dev/null 2>&1 || true
  if [ "$e" = docker ] && have systemctl; then sudo systemctl stop docker docker.socket containerd 2>/dev/null || true; fi
  echo "searxng : down"
}

case "${1:-up}" in
  up)     ollama_up ;;
  up-web) ollama_up; searxng_up ;;
  down)   ollama_down; searxng_down ;;
  *) echo "usage: ./lab.sh [up | up-web | down]"; exit 1 ;;
esac
