#!/usr/bin/env bash

# KASM images runs any script named "custom_startup.sh" at startup if it is
# located in /dockerstartup dir. Thus, this serves as the default entrypoint
# for any DTaaS specific processes and services.

set -e
if [[ ${DTAAS_DEBUG:-0} == 1 ]]; then
    set -x
fi

function cleanup {
    trap - SIGINT SIGTERM SIGQUIT SIGHUP ERR
    pkill -P $$
    exit 0
}

# Takes all subprocesses with it if this dies.
trap cleanup SIGINT SIGTERM SIGQUIT SIGHUP ERR

declare -A DTAAS_PROCS
declare -a RESTART_QUEUE

function start_nginx {
    setsid nginx -g 'daemon off;' &
    DTAAS_PROCS['nginx']=$!
}

function start_jupyter {
    jupyter notebook &
    DTAAS_PROCS['jupyter']=$!
}

function start_vscode_server {
    local persistent_dir="$1"
    code-server \
    --auth none \
    --port "${CODE_SERVER_PORT}" \
    --disable-telemetry \
    --disable-update-check \
    --user-data-dir "${HOME}/.vscode-server" \
    "${persistent_dir}" &
    DTAAS_PROCS['vscode']=$!
}

function start_admin_server {
    local path_prefix="${MAIN_USER:-}"
    if [[ -n "${path_prefix}" ]]; then
        workspace-admin --host 0.0.0.0 --port "${ADMIN_SERVER_PORT}" --path-prefix "${path_prefix}" &
    else
        workspace-admin --host 0.0.0.0 --port "${ADMIN_SERVER_PORT}" &
    fi
    DTAAS_PROCS['admin']=$!
}

# Links the persistent dir to its subdirectory in home. Can only happen after
# KASM has setup the main user home directories.
if [[ ! -h "${HOME}"/Desktop/workspace ]]; then
    ln -s "${PERSISTENT_DIR}" "${HOME}"/Desktop/workspace
fi

start_nginx
if [[ ! ${JUPYTER_DISABLED:-0} == 1 ]]; then
    start_jupyter
fi
start_admin_server
if [[ ! ${VSCODE_DISABLED:-0} == 1 ]]; then
    start_vscode_server "${PERSISTENT_DIR}"
fi

# Monitor and resurrect DTaaS services.
sleep 3
while :
do
    RESTART_QUEUE=()

    for process in "${!DTAAS_PROCS[@]}"; do
        if ! kill -0 "${DTAAS_PROCS[${process}]}" 2>/dev/null ; then
            echo "[WARNING] ${process} stopped, queuing restart"
            RESTART_QUEUE+=("${process}")
        fi
    done

    for process in "${RESTART_QUEUE[@]}"; do
        case ${process} in
            nginx)
                echo "[INFO] Restarting nginx"
                kill -- -"${DTAAS_PROCS[${process}]}"
                start_nginx
                ;;
            jupyter)
                echo "[INFO] Restarting Jupyter"
                start_jupyter
                ;;
            vscode)
                echo "[INFO] Restarting VS Code server"
                start_vscode_server "${PERSISTENT_DIR}"
                ;;
            admin)
                echo "[INFO] Restarting Admin server"
                start_admin_server
                ;;
            *)
                echo "[WARNING] An unknown service '${process}' unexpectedly monitored by the custom_startup script was reported to have exited. This is most irregular - check if something is adding processes to the custom_startup script's list of monitored subprocesses."
                ;;
        esac
    done

    sleep 3
done