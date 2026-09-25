module pivot_shift #(
    parameter WIDTH = 512,
    parameter N     = 8
)
(
    input  wire                clk,
    input  wire                rst,
    input  wire [WIDTH-1:0]    operand_a,
    input  wire [WIDTH-1:0]    operand_b,
    input  wire [N*WIDTH-1:0]  vectors_in,
    output wire [WIDTH-1:0]    shift_output
);
    // clk/rst/vectors_in unused: shift rotates operand_a using the low bits
    // of operand_b as the (wrapped) rotate amount.
    localparam SHIFT_WIDTH = (WIDTH > 1) ? $clog2(WIDTH) : 1;
    wire [SHIFT_WIDTH-1:0] encoded_amount = operand_b;
    wire [SHIFT_WIDTH-1:0] shift_amount = encoded_amount % WIDTH;

    assign shift_output = (operand_a << shift_amount) | (operand_a >> (WIDTH - shift_amount));
endmodule
