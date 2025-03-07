#! /bin/bash
sudo apt-get update
sudo apt-get install apache2 libapache2-mod-wsgi-py3 -y
sudo a2ensite default-ssl
sudo a2enmod ssl
sudo a2enmod wsgi

vm_hostname="$(curl -H "Metadata-Flavor:Google" \
http://metadata.google.internal/computeMetadata/v1/instance/name)"
echo "Page served from: $vm_hostname" | \
tee /var/www/html/index.html

dpkg -s rabbitmq-server
if [ $? -eq 1 ]; then 
	sudo apt-get install curl gnupg -y
	curl -fsSL https://packages.rabbitmq.com/gpg | sudo apt-key add -
	sudo add-apt-repository 'deb https://dl.bintray.com/rabbitmq/debian focal main'
	sudo apt update && sudo apt install rabbitmq-server -y
	sudo systemctl enable rabbitmq-server
	sudo systemctl start rabbitmq-server
fi

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

cd git/mimicBot
if [ ! -d "${HOME}/git/mimicBot/venv" ]; then
	python3 -m venv venv
	venv/bin/activate
	pip install -r requirements.txt
else
	venv/bin/activate
fi
cd ${HOME}

cd git/mimicBot 
python3 xata_frontend.py --download_cfg --cfg_id default
if [ ! -f "lib/dual_zero_v04/weights.ckpt" ]; then
	python3 xata_frontend.py --download_model --model_dir lib/dual_zero_v04 --model_id dual_zero_v04
fi

if [ ! -d /var/www/html/mimicBot ]; then
	sudo ln -sT ~/git/mimicBot /var/www/html/mimicBot
fi
sudo cp git/mimicBot/apache.conf /etc/apache2/sites-enabled/000-default.conf
sudo cp git/mimicBot/celery.conf /etc/default/celeryd
sudo cp git/mimicBot/celeryd /etc/init.d
sudo chmod 755 /etc/init.d/celeryd
sudo /etc/init.d/celeryd start

sudo systemctl restart apache2
