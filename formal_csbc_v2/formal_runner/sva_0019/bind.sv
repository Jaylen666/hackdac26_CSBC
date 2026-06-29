module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire fault_err_req_q,
    input wire op_err_req_q,
    input wire rst_ni,
    input wire clk_i
);

assert property (@(posedge clk_i) disable iff (!rst_ni) ($rose(rst_ni) |=> (fault_err_req_q == '0 && op_err_req_q == '0)));

endmodule


bind keymgr sva_checker sva_checker_inst (.*);
