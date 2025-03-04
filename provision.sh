#! /bin/bash

CONDA_VER=latest
OS_TYPE=x86_64
CONDA_DIR=${HOME}/miniconda
PY_VER=3.10
cd ${HOME}

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

sudo apt-get install git
if [ ! -d "${HOME}/git/mimicBot" ]; then
    cd git
    git clone --recurse-submodules "https://${GHTOKEN}@github.com/nrxszvo/mimicBot.git"
    cd mimicBot
    if [ -z ${MYNAME+x} ]; then
        echo "git name and email not specified; skipping git config"
    else
        git config --global user.name ${MYNAME}
        git config --global user.email ${MYEMAIL}
    fi
    cd ${HOME}
fi

conda env update --file=git/mimicBot/environment.yml

sudo apt-get update
sudo apt-get install apache2 -y
sudo 2ensite default-ssl
sudo a2enmod ssl
vm_hostname="$(curl -H "Metadata-Flavor:Google" \
http://metadata.google.internal/computeMetadata/v1/instance/name)"
echo "mimicBot server: $vm_hostname" | \
tee /var/www/html/index.html
sudo systemctl restart apache2

