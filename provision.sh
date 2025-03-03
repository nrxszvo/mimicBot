CONDA_VER=latest
OS_TYPE=$(uname -i)
CONDA_DIR=${HOME}/miniconda
PY_VER=3.10
cd ${HOME}

if [ ! -d "${CONDA_DIR}" ]; then
    echo "removing local python site-packages..."
    rm -rf ${HOME}/.local/lib/python${PY_VER}/site-packages
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

if ! command -v npm 2>&1 >/dev/null
then
    curl -fsSL https://deb.nodesource.com/setup_23.x -o nodesource_setup.sh
    sudo -E bash nodesource_setup.sh
    sudo apt-get install -y nodejs
fi

if ! command -v yarn 2>&1 > /dev/null
then
    sudo npm install --global yarn
fi

if [ ! -e "${HOME}/.vim/autoload/plug.vim" ]; then
    curl -fLo ${HOME}/.vim/autoload/plug.vim --create-dirs https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim
    sudo add-apt-repository -y ppa:jonathonf/vim
    sudo apt update
    sudo apt install -y vim
fi

if [ ! -d "${HOME}/git/vimrc" ]; then
    cd ${HOME}/git
    git clone https://github.com/nrxszvo/vimrc.git
    cp vimrc/vimrc ${HOME}/.vimrc
    cp vimrc/coc-settings.json ${HOME}/.vim/coc-settings.json
fi
i

sudo apt-get install tmux
echo "set -g mouse on" > ${HOME}/.tmux.conf

