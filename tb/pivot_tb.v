// Trusted capture harness. Expected answers and evaluator seed never enter HDL.
// The Python runner performs self-checking against the independent golden model.
`timescale 1ns/1ps
module pivot_tb;
    parameter WIDTH = 512;
    parameter N = 8;
    parameter K = 3;
    localparam COUNT_WIDTH = $clog2(N+1);
    localparam DIST_WIDTH = $clog2(WIDTH+1);
    localparam IDX_WIDTH = (N > 1) ? $clog2(N) : 1;
    localparam AUX_WIDTH = (WIDTH*COUNT_WIDTH > K*IDX_WIDTH)
                           ? WIDTH*COUNT_WIDTH : K*IDX_WIDTH;
    reg clk = 0;
    reg rst = 0;
    reg [2:0] opcode;
    reg [WIDTH-1:0] operand_a;
    reg [WIDTH-1:0] operand_b;
    reg [N*WIDTH-1:0] vectors_in;
    wire [WIDTH-1:0] result_vector;
    wire [N*DIST_WIDTH-1:0] result_distance;
    wire [AUX_WIDTH-1:0] result_aux;
    integer input_fd;
    integer scanned;
    integer case_id;
    string input_path;

    pivot_tile #(.WIDTH(WIDTH), .N(N), .K(K)) dut (
        .clk(clk), .rst(rst), .opcode(opcode),
        .operand_a(operand_a), .operand_b(operand_b), .vectors_in(vectors_in),
        .result_vector(result_vector), .result_distance(result_distance),
        .result_aux(result_aux)
    );

    initial begin
        if (!$value$plusargs("INPUT=%s", input_path))
            input_path = "stimuli.hex";
        input_fd = $fopen(input_path, "r");
        if (input_fd == 0) $fatal(1, "Cannot open stimuli");
        case_id = 0;
        while (!$feof(input_fd)) begin
            scanned = $fscanf(input_fd, "%d %h %h %h\n", opcode, operand_a, operand_b, vectors_in);
            if (scanned == 4) begin
                // Exercise both clock/reset values: combinational contract ignores them.
                clk = case_id % 2;
                rst = (case_id / 2) % 2;
                #1;
                $display("PIVOT %0d %h %h %h", case_id,
                         result_vector, result_distance, result_aux);
                case_id = case_id + 1;
            end else if (!$feof(input_fd)) begin
                $fatal(1, "Malformed stimulus record");
            end
        end
        $fclose(input_fd);
        $display("PIVOT_DONE %0d", case_id);
        $finish;
    end
endmodule
