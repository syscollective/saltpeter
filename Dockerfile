from ubuntu:focal

MAINTAINER Syscollective SRL 

COPY ./ /opt/saltpeter/

# Add the Salt repo and install basic tools
RUN apt-get update && \
    apt-get install -y -o DPkg::Options::=--force-confold wget vim net-tools htop apt-utils apt-transport-https gnupg

#RUN wget -O - https://repo.saltstack.com/apt/ubuntu/16.04/amd64/2019.2/SALTSTACK-GPG-KEY.pub | apt-key add - &&\
#    echo 'deb http://repo.saltstack.com/apt/ubuntu/16.04/amd64/2019.2 xenial main' > /etc/apt/sources.list.d/saltstack.list && \
#    apt-get update

RUN wget -O - https://repo.saltstack.com/py3/ubuntu/20.04/amd64/latest/SALTSTACK-GPG-KEY.pub | apt-key add - && \
    echo 'deb http://repo.saltstack.com/py3/ubuntu/20.04/amd64/latest focal main' > /etc/apt/sources.list.d/saltstack.list && \
    apt-get update

# Install Salt master and minion
RUN DEBIAN_FRONTEND=noninteractive \
    TZ=UTC \
    apt-get install -y -o DPkg::Options::=--force-confold \
	salt-master salt-minion

# Install pip and python-dev
RUN apt-get install -y -o DPkg::Options::=--force-confold \
	python3-dev python3-pip

# Configure salt master
RUN echo "auto_accept: True" > /etc/salt/master.d/auto_accept.conf
#RUN echo "master: saltpeter-master" > /etc/salt/minion.d/masters.conf
RUN echo "master: sp-master" > /etc/salt/minion.d/masters.conf

# Install Saltperer's dependencies
RUN pip3 install -r /opt/saltpeter/requirements.txt

EXPOSE 4505 4506

#ENTRYPOINT ["/usr/bin/salt-master", "-d", "&&", "/opt/saltpeter/saltpeter/saltpeter.py"]
ENTRYPOINT ["/opt/saltpeter/entrypoint.sh"]
