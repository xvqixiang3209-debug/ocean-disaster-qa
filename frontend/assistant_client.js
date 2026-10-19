/* Shared session transport. History is kept on the server, never accepted from the browser. */
(() => {
  'use strict';
  class AssistantConversationClient {
    constructor(fetcher = (...args) => fetch(...args)) {
      this.fetcher = fetcher;
      this.token = '';
      this.conversationId = null;
      this.selectedSid = null;
      this.matchRequestId = null;
      this.active = null;
      this.epoch = 0;
    }
    async request(path, options = {}) {
      const response = await this.fetcher(path, {credentials:'same-origin', cache:'no-store', ...options});
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error('服务连接异常，请稍后重试。'); }
      if (!response.ok) {
        const error = new Error(result.error || '暂时无法完成查询。');
        error.status = response.status; error.result = result;
        throw error;
      }
      return result;
    }
    async connect(signal) {
      const data = await this.request('/api/assistant/session', {signal});
      if (!data.token) throw new Error('问答服务未连接，请刷新后重试。');
      this.token = data.token;
    }
    reset(selection = {}) {
      const old = this.conversationId, token = this.token;
      this.epoch++;
      if (this.active) this.active.abort();
      this.active = null;
      this.conversationId = null;
      this.selectedSid = selection.sid || null;
      this.matchRequestId = selection.request_id || null;
      if (old && token) this.request('/api/assistant/conversations/'+encodeURIComponent(old), {
        method:'DELETE', headers:{'X-Assistant-Token':token}
      }).catch(() => {});
    }
    async ask(question) {
      if (this.active) throw new Error('上一条问题仍在处理，请稍候。');
      const epoch = this.epoch, controller = new AbortController();
      this.active = controller;
      const timer = setTimeout(() => controller.abort(), 180000);
      const unchanged = () => {
        if (epoch !== this.epoch) {const error = new Error('对话已切换。'); error.name='AbortError'; throw error;}
      };
      try {
        if (!this.token) await this.connect(controller.signal);
        unchanged();
        const payload = {question, conversation_id:this.conversationId,
          selected_sid:this.selectedSid, match_request_id:this.matchRequestId};
        const submit = () => this.request('/api/assistant/ask', {method:'POST', signal:controller.signal,
          headers:{'Content-Type':'application/json','X-Assistant-Token':this.token}, body:JSON.stringify(payload)});
        let data;
        try { data = await submit(); }
        catch (error) {
          unchanged();
          if (error.status !== 403) throw error;
          await this.connect(controller.signal); unchanged(); data = await submit();
        }
        unchanged();
        this.conversationId = data.conversation_id || this.conversationId;
        return data;
      } catch (error) {
        unchanged();
        if (error.result && error.result.conversation_id) this.conversationId=error.result.conversation_id;
        if (error.status===404) this.conversationId=null;
        throw error;
      } finally {
        clearTimeout(timer);
        if (epoch===this.epoch) this.active=null;
      }
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports=AssistantConversationClient;
  if (typeof window !== 'undefined') window.AssistantConversationClient=AssistantConversationClient;
})();
