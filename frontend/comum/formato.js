/** Formatação compartilhada pelos três PWAs. */

const REAIS = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
});

/** 800 → "R$ 8,00". Dinheiro é inteiro em centavos em todo o sistema. */
export function reais(centavos) {
  return REAIS.format((centavos ?? 0) / 100);
}

/** 800 → "8,00" (sem o R$, pra caber em botão pequeno). */
export function valor(centavos) {
  return ((centavos ?? 0) / 100).toFixed(2).replace(".", ",");
}

export function hora(instante) {
  const d = instante instanceof Date ? instante : new Date(instante);
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

export function plural(n, singular, plural_) {
  return `${n} ${n === 1 ? singular : plural_}`;
}
