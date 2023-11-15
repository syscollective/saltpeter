from debian:bookworm

MAINTAINER Syscollective SRL 

COPY ./ /opt/saltpeter/

# Install basic tools
RUN apt-get update && \
    apt-get install -y curl && \
    apt-get install -y -o DPkg::Options::=--force-confold wget vim net-tools htop apt-utils apt-transport-https gnupg

# Install pip and python-dev
RUN apt-get install -y -o DPkg::Options::=--force-confold \
    python3-dev python3-pip

#RUN curl -fsSL -o /usr/share/keyrings/salt-archive-keyring.gpg https://repo.saltproject.io/py3/debian/11/amd64/latest/salt-archive-keyring.gpg
#RUN echo "deb [signed-by=/usr/share/keyrings/salt-archive-keyring.gpg arch=amd64] https://repo.saltproject.io/py3/debian/11/amd64/latest bullseye main" | tee /etc/apt/sources.list.d/salt.list
RUN pip3 install salt --break-system-packages

#RUN apt-get update && \
#    apt-get install -y salt-master && \
#    apt-get install -y salt-minion 

# Configure salt master
RUN mkdir -p /etc/salt/master.d
RUN mkdir /etc/salt/minion.d
RUN echo "auto_accept: True" > /etc/salt/master.d/auto_accept.conf
#RUN echo "master: saltpeter-master" > /etc/salt/minion.d/masters.conf
RUN echo "master: sp-master" > /etc/salt/minion.d/masters.conf

# Install Saltperer's dependencies
RUN pip3 install -r /opt/saltpeter/requirements.txt --break-system-packages

EXPOSE 4505 4506

#ENTRYPOINT ["/usr/bin/salt-master", "-d", "&&", "/opt/saltpeter/saltpeter/saltpeter.py"]
ENTRYPOINT ["/opt/saltpeter/entrypoint.sh"]
