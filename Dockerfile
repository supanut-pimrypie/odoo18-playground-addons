# odoo:18 plus the one python package core_api needs for payload validation.
# jsonschema is not on PyPI-installable terms here (PEP 668 marks the
# interpreter externally managed), so take the distro package.
FROM odoo:18

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-jsonschema \
    && rm -rf /var/lib/apt/lists/*
USER odoo
