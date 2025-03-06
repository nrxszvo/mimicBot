#! /bin/bash

sudo apt-get install apache2-dev -y
sudo apt-get install libapache2-mod-wsgi-py3 -y
sudo a2enmod wsgi

dpkg -s rabbitmq-server
if [ $? -eq 1 ]; then 
	sudo apt-get install curl gnupg -y
	curl -fsSL https://packages.rabbitmq.com/gpg | sudo apt-key add -
	sudo add-apt-repository 'deb https://dl.bintray.com/rabbitmq/debian focal main'
	sudo apt update && sudo apt install rabbitmq-server -y
	sudo systemctl enable rabbitmq-server
	sudo systemctl start rabbitmq-server
fi

sudo systemctl restart apache2

sudo apt-get install git tmux python3.11 python3.11-venv -y 

echo "set -g mouse on" > ${HOME}/.tmux.conf

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
else
	cd git/mimicBot
	git pull
	cd ${HOME}
fi

if [ ! -d "${HOME}/git/mimicBot/venv" ]; then
	cd git/mimicBot
	python3 -m venv venv
	venv/bin/activate
	pip install -r requirements.txt
	cd ${HOME}
fi

if [ ! -d /var/www/html/mimicBot ]; then
	sudo ln -sT ~/git/mimicBot /var/www/html/mimicBot
fi

sudo cp git/mimicBot/celeryd /etc/init.d
sudo cp got/mimicBot/celery_config /etc/default/celeryd
sudo /etc/init.d/celeryd start
