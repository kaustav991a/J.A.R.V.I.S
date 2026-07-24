import React, { useState } from "react";

/**
 * Safe arithmetic evaluator — replaces eval() (no code injection, CSP-safe).
 * Supports + - * / %, decimal numbers, parentheses and unary +/-.
 * Throws on any invalid token or malformed expression.
 */
function safeEvaluate(expr) {
  const PREC = { "+": 1, "-": 1, "*": 2, "/": 2, "%": 2, "u-": 3, "u+": 3 };
  const RIGHT = { "u-": true, "u+": true };

  // ── tokenize ──────────────────────────────────────────────────────────
  const tokens = [];
  let i = 0;
  const s = String(expr);
  while (i < s.length) {
    const c = s[i];
    if (c === " " || c === "\t") { i++; continue; }
    if ((c >= "0" && c <= "9") || c === ".") {
      let num = "";
      while (i < s.length && ((s[i] >= "0" && s[i] <= "9") || s[i] === ".")) num += s[i++];
      if ((num.match(/\./g) || []).length > 1) throw new Error("bad number");
      tokens.push({ t: "num", v: parseFloat(num) });
      continue;
    }
    if ("+-*/%()".includes(c)) { tokens.push({ t: "op", v: c }); i++; continue; }
    throw new Error("bad char: " + c);
  }

  // ── shunting-yard → RPN (unary +/- detected by prefix position) ─────────
  const out = [];
  const ops = [];
  let prev = null;
  for (const tok of tokens) {
    if (tok.t === "num") {
      out.push(tok.v);
    } else if (tok.v === "(") {
      ops.push("(");
    } else if (tok.v === ")") {
      while (ops.length && ops[ops.length - 1] !== "(") out.push(ops.pop());
      if (!ops.length) throw new Error("mismatched )");
      ops.pop();
    } else {
      // operator: is it unary? (start, after another op, or after "(")
      let op = tok.v;
      const unaryPos = prev === null || (prev.t === "op" && prev.v !== ")");
      if (unaryPos && (op === "-" || op === "+")) op = "u" + op;
      while (
        ops.length &&
        ops[ops.length - 1] !== "(" &&
        (PREC[ops[ops.length - 1]] > PREC[op] ||
          (PREC[ops[ops.length - 1]] === PREC[op] && !RIGHT[op]))
      ) {
        out.push(ops.pop());
      }
      ops.push(op);
    }
    prev = tok;
  }
  while (ops.length) {
    const op = ops.pop();
    if (op === "(") throw new Error("mismatched (");
    out.push(op);
  }

  // ── evaluate RPN ────────────────────────────────────────────────────────
  const st = [];
  for (const tok of out) {
    if (typeof tok === "number") { st.push(tok); continue; }
    if (tok === "u-") { st.push(-st.pop()); continue; }
    if (tok === "u+") { continue; }
    const b = st.pop();
    const a = st.pop();
    if (a === undefined || b === undefined) throw new Error("malformed");
    if (tok === "+") st.push(a + b);
    else if (tok === "-") st.push(a - b);
    else if (tok === "*") st.push(a * b);
    else if (tok === "/") st.push(a / b);
    else if (tok === "%") st.push(a % b);
  }
  if (st.length !== 1 || !Number.isFinite(st[0])) throw new Error("malformed");
  return st[0];
}

const CalculatorWidget = () => {
  const [display, setDisplay] = useState("0");
  const [equation, setEquation] = useState("");

  const handleNum = (num) => {
    if (display === "0") setDisplay(num);
    else setDisplay(display + num);
  };

  const handleOp = (op) => {
    setEquation(display + " " + op + " ");
    setDisplay("0");
  };

  const calculate = () => {
    try {
      const result = safeEvaluate(equation + display);
      setDisplay(String(result));
      setEquation("");
    } catch (e) {
      setDisplay("Error");
    }
  };

  const clear = () => {
    setDisplay("0");
    setEquation("");
  };

  return (
    <div className="calculator-ui holographic-ui">
      <div className="calc-header">
        <div className="calc-equation">{equation}</div>
        <div className="calc-display">{display}</div>
      </div>
      <div className="calc-grid">
        <button onClick={clear} className="btn-func">C</button>
        <button onClick={() => setDisplay(display.slice(0, -1) || "0")} className="btn-func">DEL</button>
        <button onClick={() => handleOp("%")} className="btn-func">%</button>
        <button onClick={() => handleOp("/")} className="btn-op">÷</button>

        <button onClick={() => handleNum("7")}>7</button>
        <button onClick={() => handleNum("8")}>8</button>
        <button onClick={() => handleNum("9")}>9</button>
        <button onClick={() => handleOp("*")} className="btn-op">×</button>

        <button onClick={() => handleNum("4")}>4</button>
        <button onClick={() => handleNum("5")}>5</button>
        <button onClick={() => handleNum("6")}>6</button>
        <button onClick={() => handleOp("-")} className="btn-op">−</button>

        <button onClick={() => handleNum("1")}>1</button>
        <button onClick={() => handleNum("2")}>2</button>
        <button onClick={() => handleNum("3")}>3</button>
        <button onClick={() => handleOp("+")} className="btn-op">+</button>

        <button onClick={() => handleNum("0")} style={{ gridColumn: "span 2" }}>0</button>
        <button onClick={() => handleNum(".")}>.</button>
        <button onClick={calculate} className="btn-enter">=</button>
      </div>
    </div>
  );
};

export default CalculatorWidget;
