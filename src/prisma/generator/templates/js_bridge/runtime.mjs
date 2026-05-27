#!/usr/bin/env node
/**
 * Prisma Client Python JS bridge runtime.
 *
 * This process speaks newline-delimited JSON on stdio. stdout is reserved for
 * protocol frames only; all diagnostics, including accidental console.* calls
 * from imported modules, are redirected to stderr.
 */
import readline from 'node:readline';
import {Buffer} from 'node:buffer';
import process from 'node:process';
import {setTimeout as delay} from 'node:timers/promises';

export const PROTOCOL_VERSION = '2026-05-26.phase0.v1';

const stdoutWrite = process.stdout.write.bind(process.stdout);
const stderrWrite = process.stderr.write.bind(process.stderr);
const stderrTail = [];
const STDERR_TAIL_LIMIT = 8192;

function writeStderr(message) {
  const text = String(message);
  stderrWrite(text);
  stderrTail.push(text);
  while (stderrTail.join('').length > STDERR_TAIL_LIMIT) {
    stderrTail.shift();
  }
}

function redirectConsoleToStderr() {
  for (const level of ['log', 'info', 'warn', 'error', 'debug']) {
    console[level] = (...args) => {
      const rendered = args.map((item) => {
        if (typeof item === 'string') {
          return item;
        }
        try {
          return JSON.stringify(item);
        } catch {
          return String(item);
        }
      }).join(' ');
      writeStderr(`${rendered}\n`);
    };
  }
}

redirectConsoleToStderr();

function writeFrame(frame) {
  stdoutWrite(`${JSON.stringify(frame)}\n`);
}

function nowMs() {
  return Date.now();
}

function elapsedMeta(startedAt, extra = {}) {
  return {
    protocolVersion: PROTOCOL_VERSION,
    elapsedMs: Math.max(0, nowMs() - startedAt),
    ...extra,
  };
}

function stderrTailText() {
  const value = stderrTail.join('');
  return value.length ? value.slice(-STDERR_TAIL_LIMIT) : null;
}

function bridgeError(code, message, options = {}) {
  return {
    code,
    message,
    meta: options.meta ?? {},
    prismaCode: options.prismaCode ?? null,
    debug: {
      stack: options.stack ?? null,
      stderrTail: options.stderrTail ?? null,
    },
    retryable: options.retryable ?? false,
  };
}

class BridgeFailure extends Error {
  constructor(code, message, options = {}) {
    super(message);
    this.name = 'BridgeFailure';
    this.code = code;
    this.meta = options.meta ?? {};
    this.prismaCode = options.prismaCode ?? null;
    this.retryable = options.retryable ?? false;
    this.stackForDebug = options.stack ?? this.stack ?? null;
    this.stderrTail = options.stderrTail ?? null;
  }

  toProtocolError() {
    return bridgeError(this.code, this.message, {
      meta: this.meta,
      prismaCode: this.prismaCode,
      stack: this.stackForDebug,
      stderrTail: this.stderrTail,
      retryable: this.retryable,
    });
  }
}

class RollbackSignal extends Error {
  constructor(transactionId, reason) {
    super('Rollback requested by Python transaction controller.');
    this.name = 'RollbackSignal';
    this.transactionId = transactionId;
    this.reason = reason ?? null;
  }
}

function deferredPromise() {
  let resolve;
  let reject;
  const promise = new Promise((innerResolve, innerReject) => {
    resolve = innerResolve;
    reject = innerReject;
  });
  return {promise, resolve, reject};
}

function protocolFailure(message, meta = {}) {
  return new BridgeFailure('BRIDGE_PROTOCOL_ERROR', message, {meta});
}

function timeoutFailure(requestId) {
  return new BridgeFailure('BRIDGE_TIMEOUT', 'Bridge request exceeded timeout.', {
    meta: {requestId},
    retryable: false,
  });
}

function cancelledFailure(requestId, reason) {
  return new BridgeFailure('BRIDGE_CANCELLED', 'Bridge request was cancelled.', {
    meta: {requestId, reason: reason ?? null},
    retryable: false,
  });
}

function moduleNotFoundFailure(kind, specifier, cause) {
  if (kind === 'adapter') {
    return new BridgeFailure('ADAPTER_NOT_FOUND', `Missing Prisma driver adapter package for provider ${providerName()}.`, {
      meta: {
        provider: providerName(),
        package: adapterName(),
        install: `npm install ${adapterName()} pg`,
      },
      stack: cause?.stack ?? null,
      stderrTail: stderrTailText(),
    });
  }

  return new BridgeFailure('PRISMA_CLIENT_NOT_FOUND', 'Generated Prisma Client output could not be imported by the bridge.', {
    meta: {module: specifier},
    stack: cause?.stack ?? null,
    stderrTail: stderrTailText(),
  });
}

function providerName() {
  return process.env.PRISMA_PY_BRIDGE_PROVIDER || 'postgresql';
}

function adapterName() {
  return process.env.PRISMA_PY_BRIDGE_ADAPTER || '@prisma/adapter-pg';
}

function clientModuleName() {
  return process.env.PRISMA_PY_BRIDGE_CLIENT_MODULE || '@prisma/client';
}

function adapterModuleName() {
  return process.env.PRISMA_PY_BRIDGE_ADAPTER_MODULE || adapterName();
}

function clientVersion() {
  return process.env.PRISMA_PY_BRIDGE_CLIENT_VERSION || null;
}

function bridgeVersion() {
  return process.env.PRISMA_PY_BRIDGE_VERSION || null;
}

function lowerFirst(value) {
  if (!value) {
    return value;
  }
  return `${value[0].toLowerCase()}${value.slice(1)}`;
}

function isObject(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function assertRequestEnvelope(request) {
  if (!isObject(request)) {
    throw protocolFailure('Protocol frame must be a JSON object.');
  }
  if (typeof request.id !== 'string' || request.id.length === 0) {
    throw protocolFailure('Request id must be a non-empty string.', {field: 'id'});
  }
  if (typeof request.method !== 'string' || request.method.length === 0) {
    throw protocolFailure('Request method must be a non-empty string.', {field: 'method'});
  }
  if (!isObject(request.params)) {
    throw protocolFailure('Request params must be an object.', {field: 'params'});
  }
  if (!Number.isInteger(request.timeoutMs) || request.timeoutMs <= 0) {
    throw protocolFailure('Request timeoutMs must be a positive integer.', {field: 'timeoutMs'});
  }
}

function encodeSpecialScalars(value) {
  if (typeof value === 'bigint') {
    return {$type: 'BigInt', value: value.toString()};
  }
  if (value instanceof Date) {
    return {$type: 'DateTime', value: value.toISOString()};
  }
  if (Buffer.isBuffer(value)) {
    return {$type: 'Bytes', encoding: 'base64', value: value.toString('base64')};
  }
  if (value instanceof Uint8Array) {
    return {$type: 'Bytes', encoding: 'base64', value: Buffer.from(value).toString('base64')};
  }
  if (Array.isArray(value)) {
    return value.map((item) => encodeSpecialScalars(item));
  }
  if (isObject(value)) {
    if (value.constructor?.name === 'Decimal' && typeof value.toString === 'function') {
      return {$type: 'Decimal', value: value.toString()};
    }
    const result = {};
    for (const [key, nested] of Object.entries(value)) {
      result[key] = encodeSpecialScalars(nested);
    }
    return result;
  }
  return value;
}

function decodeTaggedScalars(value) {
  if (Array.isArray(value)) {
    return value.map((item) => decodeTaggedScalars(item));
  }
  if (!isObject(value)) {
    return value;
  }
  if (value.$type === 'BigInt') {
    return BigInt(value.value);
  }
  if (value.$type === 'DateTime') {
    return new Date(value.value);
  }
  if (value.$type === 'Bytes' && value.encoding === 'base64') {
    return Buffer.from(value.value, 'base64');
  }
  if (value.$type === 'Json') {
    return decodeTaggedScalars(value.value);
  }
  const result = {};
  for (const [key, nested] of Object.entries(value)) {
    result[key] = decodeTaggedScalars(nested);
  }
  return result;
}

function mapPrismaError(error, context = {}) {
  if (error instanceof BridgeFailure) {
    return error;
  }

  const name = error?.name ?? '';
  const code = typeof error?.code === 'string' ? error.code : null;
  const message = typeof error?.message === 'string' && error.message.length
    ? error.message
    : 'Prisma Client operation failed.';

  if (name.includes('Validation')) {
    return new BridgeFailure('PRISMA_VALIDATION_ERROR', 'Invalid Prisma Client query arguments.', {
      meta: context,
      prismaCode: code,
      stack: error?.stack ?? null,
      retryable: false,
    });
  }

  if (code?.startsWith('P')) {
    return new BridgeFailure('PRISMA_KNOWN_REQUEST_ERROR', message, {
      meta: {...context, ...(isObject(error?.meta) ? error.meta : {})},
      prismaCode: code,
      stack: error?.stack ?? null,
      retryable: false,
    });
  }

  return new BridgeFailure('PRISMA_RUNTIME_ERROR', message, {
    meta: context,
    prismaCode: code,
    stack: error?.stack ?? null,
    retryable: false,
  });
}

function parseDatasource(value) {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === 'string') {
    return {url: value};
  }
  if (isObject(value)) {
    return value;
  }
  throw new BridgeFailure('DATASOURCE_OVERRIDE_UNSUPPORTED', 'Datasource override must be null, a URL string, or an object.', {
    meta: {provider: providerName()},
  });
}

async function importRuntimeModule(kind, specifier) {
  try {
    return await import(specifier);
  } catch (error) {
    throw moduleNotFoundFailure(kind, specifier, error);
  }
}

function pickExport(module, names, kind) {
  for (const name of names) {
    if (typeof module[name] === 'function') {
      return module[name];
    }
  }
  if (typeof module.default === 'function') {
    return module.default;
  }
  if (isObject(module.default)) {
    for (const name of names) {
      if (typeof module.default[name] === 'function') {
        return module.default[name];
      }
    }
  }
  throw new BridgeFailure('BRIDGE_PROTOCOL_ERROR', `Imported ${kind} module does not expose a supported constructor.`, {
    meta: {exports: Object.keys(module)},
  });
}

class BridgeRuntime {
  constructor() {
    this.acceptingRequests = true;
    this.client = null;
    this.adapter = null;
    this.connected = false;
    this.inFlight = new Map();
    this.transactions = new Map();
    this.nextTransactionId = 0;
    this.clientModule = null;
    this.adapterModule = null;
    this.shuttingDown = false;
  }

  readyParams() {
    return {
      protocolVersion: PROTOCOL_VERSION,
      pid: process.pid,
      provider: providerName(),
      adapter: adapterName(),
      prismaClientVersion: clientVersion(),
      bridgeVersion: bridgeVersion(),
    };
  }

  baseMeta(startedAt, extra = {}) {
    return elapsedMeta(startedAt, {
      provider: providerName(),
      adapter: adapterName(),
      bridgeVersion: bridgeVersion(),
      ...extra,
    });
  }

  async loadClientConstructor() {
    if (!this.clientModule) {
      this.clientModule = await importRuntimeModule('client', clientModuleName());
    }
    return pickExport(this.clientModule, ['PrismaClient'], 'Prisma Client');
  }

  async loadAdapterConstructor() {
    if (!this.adapterModule) {
      this.adapterModule = await importRuntimeModule('adapter', adapterModuleName());
    }
    return pickExport(this.adapterModule, ['PrismaPg', 'PrismaPgAdapter', 'Adapter'], 'Prisma adapter');
  }

  async ensureClient(params = {}) {
    if (this.client) {
      return this.client;
    }

    const datasource = parseDatasource(params.datasource ?? null);
    const adapterOptions = isObject(params.adapterOptions) ? {...params.adapterOptions} : {};
    if (params.adapterOptions !== undefined && !isObject(params.adapterOptions)) {
      throw protocolFailure('adapterOptions must be an object.', {field: 'adapterOptions'});
    }

    if (datasource && providerName() !== 'postgresql') {
      throw new BridgeFailure('DATASOURCE_OVERRIDE_UNSUPPORTED', 'Datasource overrides are not enabled for this provider in JS bridge mode.', {
        meta: {provider: providerName()},
      });
    }

    if (datasource?.url) {
      adapterOptions.connectionString = datasource.url;
    } else if (!adapterOptions.connectionString && process.env.DATABASE_URL) {
      adapterOptions.connectionString = process.env.DATABASE_URL;
    }

    const PrismaClient = await this.loadClientConstructor();
    const Adapter = await this.loadAdapterConstructor();
    this.adapter = new Adapter(adapterOptions);
    const log = params.logQueries ? ['query', 'warn', 'error'] : ['warn', 'error'];
    this.client = new PrismaClient({adapter: this.adapter, log});
    return this.client;
  }

  async connect(params = {}) {
    const client = await this.ensureClient(params);
    if (typeof client.$connect === 'function') {
      await client.$connect();
    }
    this.connected = true;
    return {status: 'connected'};
  }

  async disconnect(params = {}) {
    if (params.rollbackOpenTransactions !== false) {
      await this.rollbackOpenTransactions('client-disconnect');
    }
    if (this.client && typeof this.client.$disconnect === 'function') {
      await this.client.$disconnect();
    }
    this.connected = false;
    return {status: 'disconnected'};
  }

  async healthcheck(params = {}) {
    let databaseReachable = null;
    if (params.requireDatabase === true) {
      try {
        const client = await this.ensureClient({});
        if (typeof client.$queryRawUnsafe === 'function') {
          await client.$queryRawUnsafe('SELECT 1');
        } else if (typeof client.$queryRaw === 'function') {
          await client.$queryRaw`SELECT 1`;
        } else if (typeof client.$connect === 'function') {
          await client.$connect();
        }
        databaseReachable = true;
      } catch (error) {
        throw mapPrismaError(error, {method: 'bridge.healthcheck'});
      }
    }
    return {
      status: 'ok',
      databaseReachable,
      activeTransactions: this.transactions.size,
    };
  }

  async shutdown() {
    this.acceptingRequests = false;
    this.shuttingDown = true;
    for (const [id, record] of this.inFlight) {
      if (id !== record.currentRequestId) {
        record.controller.abort(cancelledFailure(id, 'bridge-shutdown'));
      }
    }
    await this.rollbackOpenTransactions('bridge-shutdown');
    await this.disconnect({rollbackOpenTransactions: false});
    return {status: 'shutdown'};
  }

  async startTransaction(params = {}) {
    const client = await this.ensureClient({});
    if (typeof client.$transaction !== 'function') {
      throw new BridgeFailure('TRANSACTION_UNSUPPORTED', 'Generated Prisma Client does not expose $transaction.', {
        meta: {provider: providerName()},
      });
    }

    const transactionId = `tx_${process.pid}_${nowMs()}_${++this.nextTransactionId}`;
    const ready = deferredPromise();
    const close = deferredPromise();
    const options = {};
    if (Number.isInteger(params.timeoutMs) && params.timeoutMs > 0) {
      options.timeout = params.timeoutMs;
    }
    if (Number.isInteger(params.maxWaitMs) && params.maxWaitMs > 0) {
      options.maxWait = params.maxWaitMs;
    }
    if (params.isolationLevel) {
      options.isolationLevel = params.isolationLevel;
    }

    const record = {
      id: transactionId,
      state: 'starting',
      client: null,
      close,
      ready: ready.promise,
      promise: null,
    };
    this.transactions.set(transactionId, record);

    record.promise = client.$transaction(async (tx) => {
      record.client = tx;
      record.state = 'open';
      ready.resolve(tx);
      try {
        await close.promise;
        record.state = 'committed';
        return {status: 'committed'};
      } catch (error) {
        if (error instanceof RollbackSignal) {
          record.state = 'rolled_back';
          throw error;
        }
        record.state = 'failed';
        throw error;
      }
    }, Object.keys(options).length ? options : undefined)
      .catch((error) => {
        if (error instanceof RollbackSignal) {
          return {status: 'rolled_back', reason: error.reason};
        }
        throw error;
      })
      .finally(() => {
        this.transactions.delete(transactionId);
      });

    try {
      await Promise.race([
        ready.promise,
        record.promise.then(() => {
          throw new BridgeFailure('TRANSACTION_CLOSED', 'Transaction closed before it became ready.', {
            meta: {transactionId},
          });
        }),
      ]);
    } catch (error) {
      this.transactions.delete(transactionId);
      throw mapPrismaError(error, {method: 'transaction.start', transactionId});
    }

    return {transactionId};
  }

  async transactionClient(transactionId) {
    if (typeof transactionId !== 'string' || transactionId.length === 0) {
      throw protocolFailure('transactionId must be a non-empty string.', {field: 'transactionId'});
    }

    const record = this.transactions.get(transactionId);
    if (!record) {
      throw new BridgeFailure('TRANSACTION_CLOSED', 'Transaction is not open or has already been closed.', {
        meta: {transactionId},
      });
    }

    await record.ready;
    if (record.state !== 'open' || !record.client) {
      throw new BridgeFailure('TRANSACTION_CLOSED', 'Transaction is not open for queries.', {
        meta: {transactionId, transactionState: record.state},
      });
    }
    return record.client;
  }

  async commitTransaction(transactionId) {
    const record = this.transactions.get(transactionId);
    if (!record) {
      throw new BridgeFailure('TRANSACTION_CLOSED', 'Transaction is not open or has already been closed.', {
        meta: {transactionId},
      });
    }

    await record.ready;
    record.close.resolve({status: 'committed'});
    const result = await record.promise;
    if (result?.status !== 'committed') {
      throw new BridgeFailure('TRANSACTION_CLOSED', 'Transaction did not commit successfully.', {
        meta: {transactionId, transactionState: result?.status ?? record.state},
      });
    }
    return {status: 'committed'};
  }

  async rollbackTransaction(transactionId, params = {}) {
    const record = this.transactions.get(transactionId);
    if (!record) {
      return {status: 'rolled_back', transactionId, alreadyClosed: true};
    }

    await record.ready;
    record.close.reject(new RollbackSignal(transactionId, params.reason ?? 'python-rollback'));
    await record.promise;
    return {status: 'rolled_back'};
  }

  async rollbackOpenTransactions(reason) {
    const rollbacks = [];
    for (const transactionId of Array.from(this.transactions.keys())) {
      rollbacks.push(this.rollbackTransaction(transactionId, {reason}));
    }
    await Promise.allSettled(rollbacks);
  }

  async executeQuery(params, transactionId = null) {
    if (params.kind !== 'model') {
      throw protocolFailure('query.execute only supports kind="model" in this bridge slice.', {kind: params.kind});
    }
    const client = transactionId ? await this.transactionClient(transactionId) : await this.ensureClient({});
    const model = lowerFirst(params.model);
    const action = params.action;
    const delegate = client[model];
    if (!delegate || typeof delegate[action] !== 'function') {
      throw protocolFailure('Requested Prisma Client model action is not available.', {
        model: params.model,
        action,
        transactionId,
      });
    }
    try {
      return encodeSpecialScalars(await delegate[action](decodeTaggedScalars(params.args ?? {})));
    } catch (error) {
      throw mapPrismaError(error, {model: params.model, action, transactionId});
    }
  }

  async rawQuery(params, transactionId = null) {
    if (providerName() !== 'postgresql') {
      throw new BridgeFailure('RAW_QUERY_UNSUPPORTED', 'Raw queries are not enabled for this provider in JS bridge mode.', {
        meta: {provider: providerName()},
      });
    }
    const client = transactionId ? await this.transactionClient(transactionId) : await this.ensureClient({});
    const parameters = Array.isArray(params.parameters) ? decodeTaggedScalars(params.parameters) : [];
    const sql = params.sql;
    if (typeof sql !== 'string') {
      throw protocolFailure('query.raw sql must be a string.', {field: 'sql'});
    }
    const method = params.action === 'executeRaw' ? '$executeRawUnsafe' : '$queryRawUnsafe';
    if (typeof client[method] !== 'function') {
      throw new BridgeFailure('RAW_QUERY_UNSUPPORTED', 'Generated Prisma Client does not expose the requested raw query method.', {
        meta: {method},
      });
    }
    try {
      return encodeSpecialScalars(await client[method](sql, ...parameters));
    } catch (error) {
      throw mapPrismaError(error, {method, action: params.action});
    }
  }

  async batch(params, transactionId = null) {
    if (!Array.isArray(params.operations)) {
      throw protocolFailure('query.batch operations must be an array.', {field: 'operations'});
    }
    const client = transactionId ? await this.transactionClient(transactionId) : await this.ensureClient({});
    const operations = params.operations.map((operation) => this.operationPromise(operation, client));
    if (!transactionId && typeof client.$transaction === 'function') {
      try {
        return encodeSpecialScalars(await client.$transaction(operations, params.isolationLevel ? {isolationLevel: params.isolationLevel} : undefined));
      } catch (error) {
        throw mapPrismaError(error, {method: 'query.batch'});
      }
    }
    return encodeSpecialScalars(await Promise.all(operations));
  }

  operationPromise(operation, client = this.client) {
    if (!isObject(operation)) {
      throw protocolFailure('Batch operation must be an object.');
    }
    if (operation.kind !== 'model') {
      throw protocolFailure('Only model batch operations are supported in this bridge slice.', {kind: operation.kind});
    }
    const model = lowerFirst(operation.model);
    const action = operation.action;
    const delegate = client?.[model];
    if (!delegate || typeof delegate[action] !== 'function') {
      throw protocolFailure('Requested batch model action is not available.', {model: operation.model, action});
    }
    return delegate[action](decodeTaggedScalars(operation.args ?? {}));
  }

  async cancel(params) {
    const targetRequestId = params.targetRequestId;
    if (typeof targetRequestId !== 'string' || targetRequestId.length === 0) {
      throw protocolFailure('bridge.cancel requires params.targetRequestId.', {field: 'targetRequestId'});
    }
    const record = this.inFlight.get(targetRequestId);
    if (!record || record.responded) {
      return {status: 'not_found', targetRequestId};
    }
    record.controller.abort(cancelledFailure(targetRequestId, params.reason ?? 'bridge.cancel'));
    return {status: 'cancellation_requested', targetRequestId};
  }

  async dispatch(request) {
    switch (request.method) {
      case 'bridge.healthcheck':
        return await this.healthcheck(request.params);
      case 'client.connect':
        return await this.connect(request.params);
      case 'client.disconnect':
        return await this.disconnect(request.params);
      case 'bridge.shutdown':
        return await this.shutdown();
      case 'bridge.cancel':
        return await this.cancel(request.params);
      case 'query.execute':
        return await this.executeQuery(request.params, request.transactionId ?? null);
      case 'query.raw':
        return await this.rawQuery(request.params, request.transactionId ?? null);
      case 'query.batch':
        return await this.batch(request.params, request.transactionId ?? null);
      case 'transaction.start':
        return await this.startTransaction(request.params);
      case 'transaction.commit':
        return await this.commitTransaction(request.transactionId);
      case 'transaction.rollback':
        return await this.rollbackTransaction(request.transactionId, request.params);
      default:
        throw protocolFailure(`Unknown bridge method: ${request.method}`, {method: request.method});
    }
  }

  respond(record, frame) {
    if (record.responded) {
      return;
    }
    record.responded = true;
    this.inFlight.delete(record.id);
    writeFrame(frame);
  }

  handleFrame(frame) {
    let request;
    try {
      request = JSON.parse(frame);
    } catch (error) {
      writeStderr(`Malformed JSON protocol line: ${error.message}\n`);
      process.exitCode = 1;
      process.stdin.destroy();
      return;
    }

    let startedAt = nowMs();
    try {
      assertRequestEnvelope(request);
    } catch (error) {
      const failure = error instanceof BridgeFailure ? error : mapPrismaError(error);
      const id = isObject(request) && typeof request.id === 'string' ? request.id : null;
      if (id) {
        writeFrame({id, error: failure.toProtocolError(), meta: elapsedMeta(startedAt)});
      } else {
        writeStderr(`${failure.message}\n`);
        process.exitCode = 1;
        process.stdin.destroy();
      }
      return;
    }

    if (!this.acceptingRequests && request.method !== 'bridge.shutdown') {
      writeFrame({
        id: request.id,
        error: bridgeError('BRIDGE_PROCESS_EXITED', 'Bridge is shutting down and is not accepting new requests.'),
        meta: elapsedMeta(startedAt),
      });
      return;
    }

    if (this.inFlight.has(request.id)) {
      const existing = this.inFlight.get(request.id);
      existing.controller.abort(protocolFailure('Duplicate in-flight request id.', {id: request.id}));
      existing.responded = true;
      this.inFlight.delete(request.id);
      writeFrame({
        id: request.id,
        error: bridgeError('BRIDGE_PROTOCOL_ERROR', 'Duplicate in-flight request id.', {meta: {id: request.id}}),
        meta: elapsedMeta(startedAt),
      });
      return;
    }

    const controller = new AbortController();
    const record = {
      id: request.id,
      currentRequestId: request.id,
      controller,
      responded: false,
    };
    this.inFlight.set(request.id, record);

    const timeoutPromise = delay(request.timeoutMs, undefined, {signal: controller.signal})
      .then(() => {
        throw timeoutFailure(request.id);
      });

    const abortPromise = new Promise((_, reject) => {
      controller.signal.addEventListener('abort', () => {
        reject(controller.signal.reason ?? cancelledFailure(request.id, 'abort'));
      }, {once: true});
    });

    Promise.race([this.dispatch(request), timeoutPromise, abortPromise])
      .then((result) => {
        controller.abort(cancelledFailure(request.id, 'completed'));
        this.respond(record, {
          id: request.id,
          result: encodeSpecialScalars(result),
          meta: this.baseMeta(startedAt),
        });
        if (request.method === 'bridge.shutdown') {
          setImmediate(() => process.exit(0));
        }
      })
      .catch((error) => {
        const failure = error instanceof BridgeFailure ? error : mapPrismaError(error, {method: request.method});
        this.respond(record, {
          id: request.id,
          error: failure.toProtocolError(),
          meta: this.baseMeta(startedAt),
        });
      });
  }
}

const runtime = new BridgeRuntime();
writeFrame({method: 'bridge.ready', params: runtime.readyParams()});

const rl = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
rl.on('line', (line) => {
  if (line.trim().length === 0) {
    return;
  }
  runtime.handleFrame(line);
});
rl.on('close', async () => {
  if (!runtime.shuttingDown) {
    try {
      await runtime.disconnect();
    } catch (error) {
      writeStderr(`Bridge disconnect during stdin close failed: ${error?.message ?? error}\n`);
    }
  }
});
