# One image for both runtimes (design.md §1.1):
#   Lambda  – Lambda Web Adapter (extension) forwards Function URL / SQS events to uvicorn
#   Fargate – the same uvicorn process, no adapter involved
FROM public.ecr.aws/docker/library/python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM public.ecr.aws/docker/library/python:3.12-slim
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:1.1.0 /lambda-adapter /opt/extensions/lambda-adapter
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY src ./src
COPY data ./data
COPY db ./db
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MILEWISE_DATA_DIR=/app/data \
    AWS_LWA_PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/healthz \
    AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_PASS_THROUGH_PATH=/events
EXPOSE 8080
RUN useradd --uid 10001 --no-create-home app
USER 10001
CMD ["uvicorn", "milewise.api:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
