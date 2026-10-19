// Radar de Leilões — recebe as mensagens do Telegram NA HORA e conecta a conta.
//
// Quando a pessoa toca em "Iniciar" depois de escanear o QR Code do painel, o
// Telegram chama esta função (webhook). O QR Code leva um código de uso único
// (gerado pelo painel, vale 15 minutos). A função troca o código pela conta, grava
// o chat_id no perfil e apaga o código, e o bot responde na mesma hora.
//
// Segredos da função (Supabase → Edge Functions → Secrets):
//   TELEGRAM_BOT_TOKEN       token do bot (o mesmo que está no GitHub)
//   TELEGRAM_WEBHOOK_SECRET  senha inventada por você; o Telegram a envia em cada
//                            chamada e a função recusa quem não tiver a senha
// O Supabase já fornece SUPABASE_URL e a chave de serviço automaticamente.
//
// Esta função precisa ser publicada SEM verificação de JWT, porque o Telegram não
// manda JWT. A proteção é a senha acima.

import { createClient } from "npm:@supabase/supabase-js@2";

const CODIGO = /^[A-Za-z0-9_-]{16,64}$/;

function chaveDeServico(): string {
  const legada = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (legada) return legada;
  try {
    return JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") ?? "{}")["default"] ?? "";
  } catch {
    return "";
  }
}

// Toque duplo em "Iniciar" (ou o Telegram reenviando o comando): o código já foi usado,
// mas se este mesmo Telegram já está conectado, isso não é erro, então não assustamos.
async function jaEstaConectado(supabase: any, chatId: number | string): Promise<boolean> {
  const { data } = await supabase
    .from("perfil_usuario")
    .select("user_id")
    .eq("telegram_chat_id", String(chatId))
    .limit(1);
  return !!data && data.length > 0;
}

async function responder(chatId: number | string, texto: string): Promise<void> {
  const token = Deno.env.get("TELEGRAM_BOT_TOKEN") ?? "";
  try {
    const r = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: texto }),
    });
    if (!r.ok) console.error("Telegram recusou a resposta:", r.status, (await r.text()).slice(0, 200));
  } catch (e) {
    console.error("Falha ao responder no Telegram:", e);
  }
}

export async function tratar(req: Request): Promise<Response> {
  const ok = () => new Response("ok", { status: 200 });

  if (req.method !== "POST") return ok();

  // só aceita chamadas que tragam a senha combinada com o Telegram
  const senha = Deno.env.get("TELEGRAM_WEBHOOK_SECRET") ?? "";
  if (!senha || req.headers.get("x-telegram-bot-api-secret-token") !== senha) {
    return new Response("não autorizado", { status: 401 });
  }

  let update: any;
  try {
    update = await req.json();
  } catch {
    return ok();
  }

  const msg = update?.message;
  const chatId = msg?.chat?.id;
  const texto = String(msg?.text ?? "").trim();
  // só conversa privada com o bot, e só o comando /start
  if (!chatId || msg?.chat?.type !== "private" || !texto.startsWith("/start")) return ok();

  const payload = texto.split(/\s+/, 2)[1] ?? "";

  if (!payload) {
    await responder(
      chatId,
      "Olá! Para receber os avisos do Radar de Leilões, abra o painel, vá em Notificações no Telegram " +
        "e escaneie o QR Code (ou use o botão Abrir no Telegram).",
    );
    return ok();
  }

  if (!CODIGO.test(payload)) {
    await responder(chatId, "⚠️ Esse link de conexão não é válido. Abra o painel e gere o QR Code de novo.");
    return ok();
  }

  const supabase = createClient(Deno.env.get("SUPABASE_URL") ?? "", chaveDeServico(), {
    auth: { persistSession: false, autoRefreshToken: false },
  });

  // 1) o código existe e ainda não expirou?
  const { data: perfil, error: erroBusca } = await supabase
    .from("perfil_usuario")
    .select("user_id")
    .eq("vinculo_token", payload)
    .gt("vinculo_expira", new Date().toISOString())
    .maybeSingle();

  if (erroBusca) {
    console.error("Erro ao conferir o código:", erroBusca.code, erroBusca.message);
    await responder(
      chatId,
      "⚠️ Recebi o seu pedido, mas não consegui conferir o código. Avise o administrador do sistema e tente de novo depois.",
    );
    return ok();
  }
  if (!perfil) {
    if (await jaEstaConectado(supabase, chatId)) {
      await responder(chatId, "✅ Este Telegram já está conectado. Você já recebe os avisos aqui.");
    } else {
      await responder(chatId, "⚠️ Esse link expirou ou já foi usado. Abra o painel e gere o QR Code de novo.");
    }
    return ok();
  }

  // 2) troca o código pela conexão e apaga o código (o filtro por vinculo_token faz
  //    com que duas chamadas ao mesmo tempo não consigam usar o mesmo código)
  const { data: salvo, error } = await supabase
    .from("perfil_usuario")
    .update({
      telegram_chat_id: String(chatId),
      vinculo_token: null,
      vinculo_expira: null,
      atualizado_em: new Date().toISOString(),
    })
    .eq("user_id", perfil.user_id)
    .eq("vinculo_token", payload)
    .select("user_id");

  if (error) {
    console.error("Erro ao salvar o vínculo:", error.code, error.message);
    await responder(
      chatId,
      "⚠️ Recebi o seu pedido, mas não consegui salvar a conexão. Avise o administrador do sistema e tente de novo depois.",
    );
    return ok();
  }
  if (!salvo || salvo.length === 0) {
    if (await jaEstaConectado(supabase, chatId)) {
      await responder(chatId, "✅ Este Telegram já está conectado. Você já recebe os avisos aqui.");
    } else {
      await responder(chatId, "⚠️ Esse link já foi usado. Abra o painel e gere o QR Code de novo.");
    }
    return ok();
  }

  await responder(chatId, "✅ Pronto! A partir de agora você recebe aqui os avisos de imóvel do Radar de Leilões Caixa.");
  return ok();
}

Deno.serve(tratar);
