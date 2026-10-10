import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { test } from 'node:test'
import { RuntimeProcessManager } from '../runtime-process-manager.mjs'

class FakeChild extends EventEmitter {
  constructor(pid) {super();this.pid=pid;this.exitCode=null;this.signalCode=null}
  kill(signal) {this.signalCode=signal;this.emit('exit',null,signal)}
}
function fixture(overrides={}) {
  const children=[], killCalls=[]
  let shutdownRequests=0
  const manager=new RuntimeProcessManager({
    platform:'linux',
    createControlToken:()=> 'runtime-secret',
    spawnRuntime:()=>{const child=new FakeChild(5000+children.length);children.push(child);return child},
    requestShutdown:async()=>{shutdownRequests++;return false},
    spawnCommand:(_cmd,args)=>{killCalls.push(args);const killer=new FakeChild(777);queueMicrotask(()=>{killer.exitCode=0;killer.emit('exit',0,null)});return killer},
    sendSignal:(_pid,signal)=>{children.at(-1)?.kill(signal)},
    gracefulWaitMs:15,termWaitMs:15,forceWaitMs:15,
    logger:{info(){},warn(){},error(){}},...overrides,
  })
  return {manager,children,killCalls,get shutdownRequests(){return shutdownRequests}}
}
test('graceful shutdown uses owned token, no forced kill',async()=>{
  const f=fixture({requestShutdown:async token=>{
    assert.equal(token,'runtime-secret')
    queueMicrotask(()=>{const child=f.children[0];child.exitCode=0;child.emit('exit',0,null)})
    return true
  }})
  assert.equal(f.manager.start(),true)
  await f.manager.stop()
  assert.equal(f.manager.hasOwnedProcess(),false)
  assert.deepEqual(f.killCalls,[])
})
test('external backend is never stopped',async()=>{
  const f=fixture();await f.manager.stop()
  assert.equal(f.children.length,0)
  assert.equal(f.shutdownRequests,0)
})
test('cannot create two owned Python children',async()=>{
  const f=fixture()
  assert.equal(f.manager.start(),true)
  assert.equal(f.manager.start(),false)
  assert.equal(f.children.length,1)
  await f.manager.stop()
})
test('Windows fallback targets only the owned PID',async()=>{
  const f=fixture({platform:'win32'})
  f.manager.start()
  await f.manager.stop()
  assert.deepEqual(f.killCalls,[['/PID','5000','/T','/F']])
})
test('concurrent shutdown calls coalesce',async()=>{
  const f=fixture();f.manager.start()
  const a=f.manager.stop(),b=f.manager.stop()
  assert.equal(a,b);await a
})
