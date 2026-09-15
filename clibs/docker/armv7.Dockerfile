FROM ghcr.io/abtaudio/nimrum-cc-armv7

ENV INSIDE_DOCKER=1

WORKDIR /home/nimble/nimRumPkg

ENTRYPOINT [ "/bin/bash", "-l", "-c" ]
