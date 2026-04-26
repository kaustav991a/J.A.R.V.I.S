import React, { useState } from "react";

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
      // eslint-disable-next-line
      const result = eval(equation + display);
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
