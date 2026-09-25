module pivot_tile #(
    parameter WIDTH = 512,
    parameter N     = 8,
    parameter K     = 3,
    parameter AUX_WIDTH = (WIDTH*$clog2(N+1) > K*((N > 1) ? $clog2(N) : 1))
                        ? WIDTH*$clog2(N+1) : K*((N > 1) ? $clog2(N) : 1)
)
(
    input  wire                          clk,
    input  wire                          rst,
    input  wire [2:0]                    opcode,
    input  wire [WIDTH-1:0]              operand_a,
    input  wire [WIDTH-1:0]              operand_b,
    input  wire [N*WIDTH-1:0]            vectors_in,

    output reg  [WIDTH-1:0]              result_vector,
    output reg  [N*$clog2(WIDTH+1)-1:0]  result_distance,
    output reg  [AUX_WIDTH-1:0]          result_aux   // packed counts or top-k indices, zero-padded
);
    localparam OP_XOR      = 3'd0;
    localparam OP_SHIFT    = 3'd1;
    localparam OP_MAJORITY = 3'd2;
    localparam OP_COUNTER  = 3'd3;
    localparam OP_HAMMING  = 3'd4;
    localparam OP_TOPK     = 3'd5;

    localparam DIST_WIDTH  = $clog2(WIDTH+1);
    localparam COUNT_WIDTH = $clog2(N+1);
    localparam IDX_WIDTH   = (N > 1) ? $clog2(N) : 1;

    // synthesis translate_off
    initial begin
        if (WIDTH < 1 || N < 1 || K < 1 || K > N)
            $fatal(1, "pivot_tile requires WIDTH>=1, N>=1, 1<=K<=N");
        if (AUX_WIDTH < WIDTH*COUNT_WIDTH || AUX_WIDTH < K*IDX_WIDTH)
            $fatal(1, "AUX_WIDTH is too small for counts or top-k indices");
    end
    // synthesis translate_on

    wire [WIDTH-1:0]             xor_result;
    wire [WIDTH-1:0]             shift_result;
    wire [WIDTH-1:0]             majority_result;
    wire [WIDTH*COUNT_WIDTH-1:0] counter_result;
    wire [N*DIST_WIDTH-1:0]      hamming_result;
    wire [K*IDX_WIDTH-1:0]       topk_result;


    pivot_xor #(.WIDTH(WIDTH), .N(N)) u_xor (
        .clk(clk), .rst(rst),
        .operand_a(operand_a), .operand_b(operand_b), .vectors_in(vectors_in),
        .xor_output(xor_result)
    );

    pivot_shift #(.WIDTH(WIDTH), .N(N)) u_shift (
        .clk(clk), .rst(rst),
        .operand_a(operand_a), .operand_b(operand_b), .vectors_in(vectors_in),
        .shift_output(shift_result)
    );

    pivot_majority #(.WIDTH(WIDTH), .N(N)) u_majority (
        .clk(clk), .rst(rst),
        .operand_a(operand_a), .operand_b(operand_b), .vectors_in(vectors_in),
        .majority_output(majority_result)
    );

    pivot_counter #(.WIDTH(WIDTH), .N(N)) u_counter (
        .clk(clk), .rst(rst),
        .operand_a(operand_a), .operand_b(operand_b), .vectors_in(vectors_in),
        .counter_output(counter_result)
    );

    pivot_hamming #(.WIDTH(WIDTH), .N(N)) u_hamming (
        .clk(clk), .rst(rst),
        .operand_a(operand_a), .operand_b(operand_b), .vectors_in(vectors_in),
        .distance_output(hamming_result)
    );

    pivot_topk #(.WIDTH(WIDTH), .N(N), .K(K)) u_topk (
        .clk(clk), .rst(rst),
        .operand_a(operand_a), .operand_b(operand_b), .vectors_in(vectors_in),
        .topk_idx_output(topk_result)
    );

    always @(*) begin
        result_vector   = {WIDTH{1'b0}};
        result_distance = {(N*DIST_WIDTH){1'b0}};
        result_aux      = {AUX_WIDTH{1'b0}};

        case (opcode)
            OP_XOR:      result_vector = xor_result;
            OP_SHIFT:    result_vector = shift_result;
            OP_MAJORITY: result_vector = majority_result;
            OP_COUNTER:  result_aux[WIDTH*COUNT_WIDTH-1:0] = counter_result;
            OP_HAMMING:  result_distance = hamming_result;
            OP_TOPK:     result_aux[K*IDX_WIDTH-1:0] = topk_result;
            default: ; // outputs stay zero
        endcase
    end
endmodule
