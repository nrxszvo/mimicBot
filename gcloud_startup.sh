#! /bin/bash

GHTOKEN=hidden

apt-get update
apt-get install apache2 libapache2-mod-wsgi-py3 -y
a2ensite default-ssl
a2enmod ssl
a2enmod wsgi

vm_hostname="$(curl -H "Metadata-Flavor:Google" \
http://metadata.google.internal/computeMetadata/v1/instance/name)"
echo "Page served from: $vm_hostname" | \
tee /var/www/html/index.html

dpkg -s rabbitmq-server
if [ $? -eq 1 ]; then 
	apt-get install curl gnupg -y
	curl -fsSL https://packages.rabbitmq.com/gpg | sudo apt-key add -
	 add-apt-repository 'deb https://dl.bintray.com/rabbitmq/debian focal main'
	sudo apt update && sudo apt install rabbitmq-server -y
	sudo systemctl enable rabbitmq-server
	sudo systemctl start rabbitmq-server
fi

apt-get install git tmux python3.11 python3.11-venv -y 

echo "set -g mouse on" > ${HOME}/.tmux.conf

cd /var/www/html
git clone "https://${GHTOKEN}@github.com/nrxszvo/mimicBot.git"
cd mimicBot
git submodule set-url -- lib/pgnUtils "https://${GHTOKEN}@github.com/nrxszvo/pgnUtils.git"
git submodule update --init --recursive
echo -e "XATA_BRANCH=main\nXATA_API_KEY=hidden" | tee .env
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

python3 xata_frontend.py --download_cfg --cfg_id default
if [ ! -f "lib/dual_zero_v04/weights.ckpt" ]; then
	python3 xata_frontend.py --download_model --model_dir lib/dual_zero_v04 --model_id dual_zero_v04
fi
sudo mkdir /var/www/.config
sudo mkdir /var/www/.config/xata
sudo cp .env /var/www/.config/xata/key
sudo cp .xatarc /
cp apache.conf /etc/apache2/sites-enabled/000-default.conf
cp celery.conf /etc/default/celeryd
cp celeryd /etc/init.d
chmod 755 /etc/init.d/celeryd
/etc/init.d/celeryd start

systemctl restart apache2
