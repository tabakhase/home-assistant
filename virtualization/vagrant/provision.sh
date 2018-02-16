#!/bin/bash
set -e

readonly SETUP_DONE='/home-assistant/virtualization/vagrant/setup_done'
readonly RUN_TESTS='/home-assistant/virtualization/vagrant/run_tests'
readonly RESTART='/home-assistant/virtualization/vagrant/restart'

readonly UI_REPO_REL='home-assistant-polymer'
readonly RUN_UI_SETUP='/home-assistant/virtualization/vagrant/run_ui_setup'
readonly RUN_UI_BOOTSTRAP='/home-assistant/virtualization/vagrant/run_ui_bootstrap'
readonly RUN_UI_DEV='/home-assistant/virtualization/vagrant/run_ui_dev'
readonly RUN_UI_DEV_WATCH='/home-assistant/virtualization/vagrant/run_ui_dev_watch'
readonly RUN_UI_TEST='/home-assistant/virtualization/vagrant/run_ui_test'
readonly RUN_UI_BUILD='/home-assistant/virtualization/vagrant/run_ui_build'

usage() {
    echo '############################################################

Use `./provision.sh` to interact with HASS. E.g:

- setup the environment: `./provision.sh start`
- restart HASS process: `./provision.sh restart`
- run test suit: `./provision.sh tests`
- destroy the host and start anew: `./provision.sh recreate`

Official documentation at https://home-assistant.io/docs/installation/vagrant/

############################################################'
}

print_done() {
    echo '############################################################


HASS running => http://localhost:8123/

'
}

setup_error() {
    echo '############################################################
Something is off... maybe setup did not complete properly?
Please ensure setup did run correctly at least once.

To run setup again: `./provision.sh setup`

############################################################'
    exit 1
}

setup() {
    local hass_path='/root/venv/bin/hass'
    local systemd_bin_path='/usr/bin/hass'
    # Setup systemd
    cp /home-assistant/virtualization/vagrant/home-assistant@.service \
        /etc/systemd/system/home-assistant.service
    systemctl --system daemon-reload
    systemctl enable home-assistant
    systemctl stop home-assistant
    # Install packages
    apt-get update
    apt-get install -y git rsync python3-dev python3-pip libssl-dev libffi-dev
    pip3 install --upgrade virtualenv
    virtualenv ~/venv
    source ~/venv/bin/activate
    pip3 install --upgrade tox
    /home-assistant/script/setup
    if ! [ -f $systemd_bin_path ]; then
        ln -s $hass_path $systemd_bin_path
    fi
    touch $SETUP_DONE
    print_done
    usage
}

run_tests() {
    rm -f $RUN_TESTS
    echo '############################################################'
    echo; echo "Running test suite, hang on..."; echo; echo
    if ! systemctl stop home-assistant; then
        setup_error
    fi
    source ~/venv/bin/activate
    rsync -a --delete \
        --exclude='*.tox' \
        --exclude='*.git' \
        /home-assistant/ /home-assistant-tests/
    cd /home-assistant-tests && tox || true
    echo '############################################################'
}

restart() {
    echo "Restarting Home Assistant..."
    if ! systemctl restart home-assistant; then
        setup_error
    else
        echo "done"
    fi
    rm $RESTART
}

check_local_uirepo() {
    if [ ! -d "./$UI_REPO_REL" ]; then
        echo '############################################################
Error: No local '$UI_REPO_REL' repo found"

To be able to use these commands clone the repository into this directory,
and edit your configuration.yaml to point to:
 ---
 frontend:
   development_repo: "/home-assistant/virtualization/vagrant/'$UI_REPO_REL'"
 ---
############################################################'
        exit 1
    fi
}

ui_envloader() {
    export NVM_DIR="$HOME/.nvm"
    if [ -s "$NVM_DIR/nvm.sh" ]; then
        source "$NVM_DIR/nvm.sh" # This loads nvm
    fi
    cd "/home-assistant/virtualization/vagrant/$UI_REPO_REL"
    nvm use || true # bypass first install failure
}

run_ui_setup() {
    rm -f $RUN_UI_SETUP
    if [ $(dpkg-query -W -f='${Status}' curl 2>/dev/null | grep -c "ok installed") == "0" ] && \
      [ $(dpkg-query -W -f='${Status}' apt-transport-https 2>/dev/null | grep -c "ok installed") == "0" ]; then
        apt-get update && apt-get install curl apt-transport-https -y
    fi
    ui_envloader
    if ! command -v nvm >/dev/null; then
        curl -o- https://raw.githubusercontent.com/creationix/nvm/v0.33.8/install.sh | bash
        ui_envloader
    fi
    nvm install
    if ! hash yarn >/dev/null; then
        curl -sS https://dl.yarnpkg.com/debian/pubkey.gpg | apt-key add -
        echo "deb https://dl.yarnpkg.com/debian/ stable main" | tee /etc/apt/sources.list.d/yarn.list > /dev/null
        apt-get update && apt-get install yarn -y
    fi
    script/bootstrap
}

run_ui_bootstrap() {
    rm -f $RUN_UI_BOOTSTRAP
    ui_envloader
    script/bootstrap
}

run_ui_dev() {
    rm -f $RUN_UI_DEV
    ui_envloader
    yarn dev
}
run_ui_dev_watch() {
    rm -f $RUN_UI_DEV_WATCH
    ui_envloader
    # trap ctrl-c and call ctrl_c()
    trap run_ui_dev_watch_ctrl_c_inner SIGINT SIGTERM
    function run_ui_dev_watch_ctrl_c_inner() {
        echo "** trapped ctrl c"
        if pidof gulp; then
            kill $(pidof gulp)
        fi
        exit
    }
    yarn dev-watch
}

run_ui_test() {
    rm -f $RUN_UI_TEST
    ui_envloader
    yarn test
}

run_ui_build() {
    rm -f $RUN_UI_BUILD
    ui_envloader
    script/build_frontend
}

main() {
    # If a parameter is provided, we assume it's the user interacting
    # with the provider script...
    case $1 in
        "setup") rm -f setup_done; vagrant up --provision && touch setup_done; exit ;;
        "tests") touch run_tests; vagrant provision ; exit ;;
        "restart") touch restart; vagrant provision ; exit ;;
        "start") vagrant up --provision ; exit ;;
        "stop") vagrant halt ; exit ;;
        "destroy") vagrant destroy -f ; exit ;;
        "recreate") rm -f setup_done restart; vagrant destroy -f; \
                    vagrant up --provision; exit ;;
        "ui-setup") check_local_uirepo; touch run_ui_setup; vagrant provision ; exit ;;
        "ui-bootstrap") check_local_uirepo; touch run_ui_bootstrap; vagrant provision ; exit ;;
        "ui-dev") check_local_uirepo; touch run_ui_dev; vagrant provision ; exit ;;
        "ui-dev-watch") check_local_uirepo; touch run_ui_dev_watch;
                    # trap ctrl-c and take care of gulp...
                    trap run_ui_dev_watch_ctrl_c_outer SIGINT SIGTERM
                    function run_ui_dev_watch_ctrl_c_outer() {
                        echo "** trapped ctrl c"
                        vagrant ssh -c 'if pidof gulp; then sudo kill $(pidof gulp); fi'
                        exit
                    }
                    vagrant provision ; exit ;;
        "ui-test") check_local_uirepo; touch run_ui_test; vagrant provision ; exit ;;
        "ui-build") check_local_uirepo; touch run_ui_build; vagrant provision ; exit ;;
    esac
    # ...otherwise we assume it's the Vagrant provisioner
    if [ $(hostname) != "contrib-jessie" ] && [ $(hostname) != "contrib-stretch" ]; then usage; exit; fi
    if ! [ -f $SETUP_DONE ]; then setup; fi
    if [ -f $RESTART ]; then restart; fi
    if [ -f $RUN_TESTS ]; then run_tests; fi
    # ui- commands call an exit after
    if [ -f $RUN_UI_SETUP ]; then run_ui_setup; exit; fi
    if [ -f $RUN_UI_BOOTSTRAP ]; then run_ui_bootstrap; exit; fi
    if [ -f $RUN_UI_DEV ]; then run_ui_dev; exit; fi
    if [ -f $RUN_UI_DEV_WATCH ]; then run_ui_dev_watch; exit; fi
    if [ -f $RUN_UI_TEST ]; then run_ui_test; exit; fi
    if [ -f $RUN_UI_BUILD ]; then run_ui_build; exit; fi
    if ! systemctl start home-assistant; then
        setup_error
    fi
}

main $*
