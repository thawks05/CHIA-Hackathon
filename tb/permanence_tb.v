`timescale 1ns/1ps
module permanence_tb;
    parameter LANES = 8;
    parameter COUNTER_WIDTH = 8;
    reg [LANES*COUNTER_WIDTH-1:0] permanence_in;
    reg [LANES-1:0] active;
    reg [COUNTER_WIDTH-1:0] increment;
    reg [COUNTER_WIDTH-1:0] decrement;
    wire [LANES*COUNTER_WIDTH-1:0] permanence_out;
    integer input_fd, scanned, case_id;
    string input_path;
    pivot_permanence #(.LANES(LANES), .COUNTER_WIDTH(COUNTER_WIDTH)) dut (
        .permanence_in(permanence_in), .active(active), .increment(increment),
        .decrement(decrement), .permanence_out(permanence_out)
    );
    initial begin
        if (!$value$plusargs("INPUT=%s", input_path)) input_path = "stimuli.hex";
        input_fd = $fopen(input_path, "r");
        if (input_fd == 0) $fatal(1, "Cannot open permanence stimuli");
        case_id = 0;
        while (!$feof(input_fd)) begin
            scanned = $fscanf(input_fd, "%h %h %h %h\n", permanence_in, active, increment, decrement);
            if (scanned == 4) begin
                #1;
                $display("PERMANENCE %0d %h", case_id, permanence_out);
                case_id = case_id + 1;
            end else if (!$feof(input_fd)) $fatal(1, "Malformed permanence stimulus");
        end
        $fclose(input_fd);
        $display("PERMANENCE_DONE %0d", case_id);
        $finish;
    end
endmodule
