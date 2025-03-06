#! /bin/bash

CONDA_VER=latest
OS_TYPE=x86_64
CONDA_DIR=${HOME}/miniconda
PY_VER=3.10
cd ${HOME}

sudo apt-get install apache2-dev -y
sudo apt-get install libapache2-mod-wsgi-py3 -y
sudo a2enmod wsgi
sudo apt-get install curl gnupg -y
curl -fsSL https://packages.rabbitmq.com/gpg | sudo apt-key add -
sudo add-apt-repository 'deb https://dl.bintray.com/rabbitmq/debian focal main'
sudo apt update && sudo apt install rabbitmq-server -y
sudo systemctl enable rabbitmq-server
sudo systemctl start rabbitmq-server
sudo systemctl restart apache2
sudo apt-get install git -y 

if [ ! -d "${CONDA_DIR}" ]; then
    echo "installing conda..."
    curl -LO "http://repo.continuum.io/miniconda/Miniconda3-${CONDA_VER}-Linux-${OS_TYPE}.sh"
    bash Miniconda3-${CONDA_VER}-Linux-${OS_TYPE}.sh -p ${HOME}/miniconda -b
    rm Miniconda3-${CONDA_VER}-Linux-${OS_TYPE}.sh
fi

if ! command -v conda 2>&1 >/dev/null
then
    export PATH=${HOME}/miniconda/bin:${PATH}
fi

conda update -y conda
conda init

if [ ! -d "${HOME}/git" ]; then
    mkdir ${HOME}/git
fi

if [ ! -d "${HOME}/git/mimicBot" ]; then
    cd git
    git clone "https://${GHTOKEN}@github.com/nrxszvo/mimicBot.git"
    cd mimicBot
	git submodule set-url -- lib/pgnUtils "https://${GHTOKEN}@github.com/nrxszvo/pgnUtils.git"
	git submodule update --init --recursive
    if [ -z ${MYNAME+x} ]; then
        echo "git name and email not specified; skipping git config"
    else
        git config --global user.name ${MYNAME}
        git config --global user.email ${MYEMAIL}
    fi
    cd ${HOME}
fi

conda env update --file=git/mimicBot/environment.yml


