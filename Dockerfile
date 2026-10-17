# syntax=docker/dockerfile:1.6
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS dataset

ADD --checksum=sha256:b0d1e0e2e8d1d49fd693e0119068f2fd69e978fe6bf362c3d0a97dc794e25675 \
    https://github.com/DeepSoftwareAnalytics/RepoTransBench/releases/download/v1.0/projects.tar.gz \
    /tmp/projects.tar.gz
RUN mkdir /dataset && tar -xzf /tmp/projects.tar.gz -C /dataset

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY repotransbench.py run.py failures.py ./
COPY prompts/ ./prompts/

# Only the five audited cases and official metadata enter the image.
COPY --from=dataset /dataset/target_projects/projects_summary.jsonl /dataset/target_projects/
COPY --from=dataset /dataset/source_projects/Java/aventrix_jnanoid /dataset/source_projects/Java/aventrix_jnanoid
COPY --from=dataset /dataset/source_projects/Java/agarciadom_xeger /dataset/source_projects/Java/agarciadom_xeger
COPY --from=dataset /dataset/source_projects/Java/editorconfig_editorconfig-jetbrains /dataset/source_projects/Java/editorconfig_editorconfig-jetbrains
COPY --from=dataset /dataset/source_projects/Java/txusballesteros_fit-chart /dataset/source_projects/Java/txusballesteros_fit-chart
COPY --from=dataset /dataset/source_projects/Java/googlearchive_android-BluetoothLeGatt /dataset/source_projects/Java/googlearchive_android-BluetoothLeGatt
COPY --from=dataset /dataset/target_projects/Java/Python/aventrix_jnanoid /dataset/target_projects/Java/Python/aventrix_jnanoid
COPY --from=dataset /dataset/target_projects/Java/Python/agarciadom_xeger /dataset/target_projects/Java/Python/agarciadom_xeger
COPY --from=dataset /dataset/target_projects/Java/Python/editorconfig_editorconfig-jetbrains /dataset/target_projects/Java/Python/editorconfig_editorconfig-jetbrains
COPY --from=dataset /dataset/target_projects/Java/Python/txusballesteros_fit-chart /dataset/target_projects/Java/Python/txusballesteros_fit-chart
COPY --from=dataset /dataset/target_projects/Java/Python/googlearchive_android-BluetoothLeGatt /dataset/target_projects/Java/Python/googlearchive_android-BluetoothLeGatt

RUN python repotransbench.py --dataset /dataset --work-dir /tmp/repotransbench-validation --list
CMD ["python", "/app/run.py", "--dataset", "/dataset", "--work-dir", "/scratch/work", "--results-dir", "/results"]
