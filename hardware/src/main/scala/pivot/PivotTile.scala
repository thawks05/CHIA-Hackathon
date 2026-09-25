package pivot

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage

/** Combinational reference architecture. No claimed cycle, area, or timing result.
  * Packed vector/count/index field zero is always in the least significant bits.
  * Opcodes preserve the original Verilog ABI; permanence is a separate module.
  */
class PivotTile(val width: Int = 512, val n: Int = 8, val k: Int = 3) extends RawModule {
  require(width >= 1 && n >= 1 && k >= 1 && k <= n)
  private val countWidth = log2Ceil(n + 1)
  private val distanceWidth = log2Ceil(width + 1)
  private val indexWidth = math.max(1, log2Ceil(n))
  private val shiftWidth = math.max(1, log2Ceil(width))
  private val auxWidth = math.max(width * countWidth, k * indexWidth)

  val clk = IO(Input(Clock())) // ABI only: combinational baseline has no state.
  val rst = IO(Input(Bool()))
  val opcode = IO(Input(UInt(3.W)))
  val operand_a = IO(Input(UInt(width.W)))
  val operand_b = IO(Input(UInt(width.W)))
  val vectors_in = IO(Input(UInt((n * width).W)))
  val result_vector = IO(Output(UInt(width.W)))
  val result_distance = IO(Output(UInt((n * distanceWidth).W)))
  val result_aux = IO(Output(UInt(auxWidth.W)))

  val vectors = (0 until n).map(i => vectors_in((i + 1) * width - 1, i * width))
  val counts = VecInit((0 until width).map(bit => PopCount(vectors.map(_(bit)))))
  val threshold = Wire(UInt(countWidth.W))
  threshold := operand_a // exact Verilog zero extension or low-bit truncation
  val majority = VecInit((0 until width).map(bit => counts(bit) >= threshold)).asUInt
  val distances = VecInit(vectors.map(vector => PopCount(vector ^ operand_a)))

  val rotated = Wire(UInt(width.W))
  if (width == 1) {
    rotated := operand_a
  } else {
    val encoded = Wire(UInt(shiftWidth.W))
    encoded := operand_b
    val amount = encoded % width.U
    rotated := ((operand_a << amount) | (operand_a >> (width.U - amount)))(width - 1, 0)
  }

  // Unrolled stable selection network. Equal distances select the smaller index.
  val selected = Wire(Vec(k, UInt(indexWidth.W)))
  var used: Seq[Bool] = Seq.fill(n)(false.B)
  for (rank <- 0 until k) {
    val winner = (0 until n).foldLeft((false.B, 0.U(distanceWidth.W), 0.U(indexWidth.W))) {
      case ((valid, bestDistance, bestIndex), index) =>
        val choose = !used(index) && (!valid || distances(index) < bestDistance)
        (valid || !used(index), Mux(choose, distances(index), bestDistance),
          Mux(choose, index.U(indexWidth.W), bestIndex))
    }
    selected(rank) := winner._3
    used = (0 until n).map(index => used(index) || winner._3 === index.U)
  }

  result_vector := 0.U
  result_distance := 0.U
  result_aux := 0.U
  switch(opcode) {
    is(0.U) { result_vector := operand_a ^ operand_b }
    is(1.U) { result_vector := rotated }
    is(2.U) { result_vector := majority }
    is(3.U) { result_aux := counts.asUInt }
    is(4.U) { result_distance := distances.asUInt }
    is(5.U) { result_aux := selected.asUInt }
  }
}

/** The sixth planned primitive, separate from the historical vote-counter opcode.
  * Active lanes add increment with saturation; inactive lanes subtract decrement.
  */
class PivotPermanence(val lanes: Int = 8, val counterWidth: Int = 8) extends RawModule {
  require(lanes >= 1 && counterWidth >= 1)
  val permanence_in = IO(Input(UInt((lanes * counterWidth).W)))
  val active = IO(Input(UInt(lanes.W)))
  val increment = IO(Input(UInt(counterWidth.W)))
  val decrement = IO(Input(UInt(counterWidth.W)))
  val permanence_out = IO(Output(UInt((lanes * counterWidth).W)))
  val updated = Wire(Vec(lanes, UInt(counterWidth.W)))
  for (lane <- 0 until lanes) {
    val value = permanence_in((lane + 1) * counterWidth - 1, lane * counterWidth)
    val sum = value +& increment
    val increased = Mux(sum(counterWidth), ((BigInt(1) << counterWidth) - 1).U, sum(counterWidth - 1, 0))
    val decreased = Mux(value < decrement, 0.U, value - decrement)
    updated(lane) := Mux(active(lane), increased, decreased)
  }
  permanence_out := updated.asUInt
}

object Elaborate extends App {
  val usage = "tile WIDTH N K OUTPUT_DIR [--chirrtl] | permanence LANES COUNTER_WIDTH OUTPUT_DIR [--chirrtl]"
  require(args.nonEmpty, usage)
  val chirrtlOnly = args.lastOption.contains("--chirrtl")
  val options = if (chirrtlOnly) args.dropRight(1) else args
  val (generator, outputDir): (() => RawModule, String) = options(0) match {
    case "tile" =>
      require(options.length == 5, usage)
      (() => new PivotTile(options(1).toInt, options(2).toInt, options(3).toInt), options(4))
    case "permanence" =>
      require(options.length == 4, usage)
      (() => new PivotPermanence(options(1).toInt, options(2).toInt), options(3))
    case _ => throw new IllegalArgumentException(usage)
  }
  if (chirrtlOnly) {
    val destination = java.nio.file.Paths.get(outputDir)
    java.nio.file.Files.createDirectories(destination)
    java.nio.file.Files.writeString(destination.resolve("design.fir"), ChiselStage.emitCHIRRTL(generator()))
  } else {
    ChiselStage.emitSystemVerilogFile(generator(), Array("--target-dir", outputDir))
  }
}
