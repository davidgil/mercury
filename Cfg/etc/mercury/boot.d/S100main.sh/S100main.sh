#!/bin/bash

# Main/Global Boot Script

# Copy template to root, rename and make executable
cp /etc/mercury/template /etc/mercury/server_tuneables
chown 755 /etc/mercury/server_tuneables

# Postfix
if [[ -a /usr/local/bin/ec2-metadata ]]; then
    REAL_HOSTNAME=$(/usr/local/bin/ec2-metadata -p | sed 's/public-hostname: //')
else
    REAL_HOSTNAME=`hostname`
fi

echo $REAL_HOSTNAME > /etc/mailname
postconf -e "myhostname = ${REAL_HOSTNAME}"
postconf -e "mydomain = ${REAL_HOSTNAME}"
postconf -e "mydestination = ${REAL_HOSTNAME}, localhost"
/etc/init.d/postfix restart

# Phone home - helps us to know how many users there are without passing any 
# identifying or personal information to us.
ID=`hostname -f | md5sum | sed 's/[^a-zA-Z0-9]//g'`
curl "http://getpantheon.com/pantheon.php?id=$ID&product=mercury"
