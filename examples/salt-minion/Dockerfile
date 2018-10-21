from ubuntu:xenial

MAINTAINER Syscollective SRL 

# Add the Salt repo
RUN apt-get update && \
    apt-get install -y -o DPkg::Options::=--force-confold wget

RUN wget -O - https://repo.saltstack.com/apt/ubuntu/16.04/amd64/latest/SALTSTACK-GPG-KEY.pub | apt-key add - && \
    echo 'deb http://repo.saltstack.com/apt/ubuntu/16.04/amd64/latest xenial main' > /etc/apt/sources.list.d/saltstack.list && \
    apt-get update

# Install Salt master and minion
RUN apt-get install -y -o DPkg::Options::=--force-confold \
	salt-minion

# Install pip and python-dev
RUN apt-get install -y -o DPkg::Options::=--force-confold \
	python-dev python-pip

# Configure salt master
RUN echo "master: sp-master" > /etc/salt/minion.d/masters.conf

ENTRYPOINT ["/usr/bin/salt-minion", "-l", "info"]
