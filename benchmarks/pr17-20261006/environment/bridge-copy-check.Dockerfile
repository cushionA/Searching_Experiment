FROM pr17-fixture-browser
WORKDIR /app
COPY server.cjs navigation-gate.cjs entrypoint.sh package.json package-lock.json ./
RUN chmod 755 /app/entrypoint.sh && chmod 644 /app/server.cjs /app/navigation-gate.cjs /app/package.json /app/package-lock.json
USER node
RUN node -e 'const g=require("./navigation-gate.cjs"); if(typeof g.waitForDocument!=="function" || typeof g.waitForReadyCondition!=="function")process.exit(1)'
