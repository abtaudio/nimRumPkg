FROM ghcr.io/abtaudio/nimrum-cc-arm64

ENV INSIDE_DOCKER=1

WORKDIR /home/nimble/nimRumPkg

ENTRYPOINT [ "/bin/bash", "-l", "-c" ]
