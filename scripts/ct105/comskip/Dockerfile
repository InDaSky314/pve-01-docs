# comskip runtime image — commercial detection + cutting for DVR post-processing
# binary compiled 2026-08-15 from erikkaashoek/Comskip master in a debian:trixie
# builder (matching glibc); rebuild recipe in docs repo.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      libargtable2-0 libavcodec61 libavformat61 libavutil59 libswscale8 \
      ffmpeg python3 \
    && rm -rf /var/lib/apt/lists/*
COPY comskip /usr/local/bin/comskip
RUN chmod +x /usr/local/bin/comskip
